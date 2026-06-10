#!/bin/bash

VERSION="v2.0"
echo -ne "\033]0;DOCKER-AUTO-RUN (${VERSION})\007"

# 0. 把当前用户加入到 docker 用户组
# sudo usermod -aG docker $USER
# reboot
# 安装 sudo apt install sshpass

# 1. 获取当前脚本所在目录，并读取配置文件
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${DIR}/run_docker.conf"

if [ -f "$CONFIG_FILE" ]; then
    source <(tr -d '\r' < "$CONFIG_FILE")
else
    echo "❌ 错误: 找不到配置文件 $CONFIG_FILE"
    echo "请确保 run_docker.conf 和本脚本在同一目录下。"
    read -p "按回车键退出..."
    exit 1
fi

cp set_env.sh $HOST_PATH
echo "✅ 成功读取配置:"
echo "   宿主机路径: $HOST_PATH"
echo "   容器内路径: $CONTAINER_PATH"
echo "----------------------------------------"

# 2. 检查并 load 远程配置中缺失的 Docker Images
if [[ -n "${REMOTE_HOST:-}" && -n "${REMOTE_CONFIG:-}" && -n "${REMOTE_IMAGE_DIR:-}" && -n "${IMAGE_FILTER:-}" ]]; then
    echo "正在检查镜像更新..."

    _DOWNLOAD_DIR="${DOWNLOAD_DIR:-/tmp/docker_images}"
    _LOCAL_CONFIG="$_DOWNLOAD_DIR/remote_config.txt"
    mkdir -p "$_DOWNLOAD_DIR"

    # 判断是否本机：提取 REMOTE_HOST 中的 IP，与本机所有 IP 对比
    _REMOTE_IP=$(echo "$REMOTE_HOST" | awk -F'@' '{print $NF}')
    _LOCAL_IPS=$(hostname -I 2>/dev/null || ip addr show | awk '/inet / {print $2}' | cut -d/ -f1)
    _IS_LOCAL=false
    for _ip in $_LOCAL_IPS; do
        if [[ "$_ip" == "$_REMOTE_IP" ]]; then
            _IS_LOCAL=true
            break
        fi
    done

    _fetch_ok=false
    if [[ "$_IS_LOCAL" == "true" ]]; then
        echo "检测到本机即为镜像主机，直接读取本地文件..."
        if cp "$REMOTE_CONFIG" "$_LOCAL_CONFIG" 2>/dev/null; then
            _fetch_ok=true
        else
            echo "⚠️  无法读取配置文件 $REMOTE_CONFIG，跳过镜像更新检查。"
        fi
    else
        read -ra _SSH_OPTS <<< "${SSH_OPTS:--q -o StrictHostKeyChecking=no -o ConnectTimeout=10}"
        _scp() {
            if [[ -n "${REMOTE_PASS:-}" ]]; then
                sshpass -p "$REMOTE_PASS" scp "${_SSH_OPTS[@]}" "$@"
            else
                scp "${_SSH_OPTS[@]}" "$@"
            fi
        }
        _ssh() {
            if [[ -n "${REMOTE_PASS:-}" ]]; then
                sshpass -p "$REMOTE_PASS" ssh "${_SSH_OPTS[@]}" "$@"
            else
                ssh "${_SSH_OPTS[@]}" "$@"
            fi
        }
        _rsync() {
            local _src="$1" _dst="$2"
            if [[ -n "${REMOTE_PASS:-}" ]]; then
                sshpass -p "$REMOTE_PASS" rsync -avP --no-compress -e "ssh ${_SSH_OPTS[*]}" "$_src" "$_dst"
            else
                rsync -avP --no-compress -e "ssh ${_SSH_OPTS[*]}" "$_src" "$_dst"
            fi
        }
        if [[ -n "${REMOTE_PASS:-}" ]] && ! command -v sshpass >/dev/null 2>&1; then
            echo "⚠️  REMOTE_PASS 已设置但 sshpass 未安装，跳过镜像更新检查。(apt install sshpass)"
        elif ! _scp "$REMOTE_HOST:$REMOTE_CONFIG" "$_LOCAL_CONFIG"; then
            echo "⚠️  无法获取远程配置文件，跳过镜像更新检查。"
        else
            _fetch_ok=true
        fi
    fi

    if [[ "$_fetch_ok" == "true" ]]; then
        _ENTRIES=$(awk -v filter="$IMAGE_FILTER" '
            /^[[:space:]]*(#|$)/ { next }
            {
                split($0, parts, /[[:space:]]*\|[[:space:]]*/);
                img = parts[1]; tar = parts[2]
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", img)
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", tar)
                if (img != "" && tar != "" && index(img, filter) > 0)
                    print img "|" tar
            }
        ' "$_LOCAL_CONFIG")
        rm -f "$_LOCAL_CONFIG"

        if [[ -z "$_ENTRIES" ]]; then
            echo "⚠️  配置中未找到匹配 '$IMAGE_FILTER' 的条目，跳过。"
        else
            # 先过滤出本地缺失的镜像
            _MISSING=""
            while IFS='|' read -r _IMG _TAR; do
                [[ -z "$_IMG" || -z "$_TAR" ]] && continue
                if docker image inspect "$_IMG" >/dev/null 2>&1; then
                    echo "✅ 镜像已存在，跳过: $_IMG"
                else
                    _MISSING+="$_IMG|$_TAR"$'\n'
                fi
            done <<< "$_ENTRIES"

            if [[ -n "$_MISSING" ]]; then
                echo ""
                echo "📋 以下镜像本地缺失，需要更新:"
                while IFS='|' read -r _IMG _TAR; do
                    [[ -z "$_IMG" ]] && continue
                    echo "   • $_IMG  ($_TAR)"
                done <<< "$_MISSING"
                echo ""
                read -p "是否在后台下载并加载？完成后日志写入 $_DOWNLOAD_DIR/update.log [Y/n]: " _CONFIRM
                if [[ ! "$_CONFIRM" =~ ^[Nn]$ ]]; then
                    _BG_LOG="$_DOWNLOAD_DIR/update.log"
                    # 后台执行：所有变量和函数在子进程中均可见
                    (
                        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 开始后台镜像更新" > "$_BG_LOG"
                        while IFS='|' read -r _IMG _TAR; do
                            [[ -z "$_IMG" || -z "$_TAR" ]] && continue
                            echo "[$(date '+%Y-%m-%d %H:%M:%S')] 处理: $_IMG" >> "$_BG_LOG"
                            if [[ "$_IS_LOCAL" == "true" ]]; then
                                echo "[$(date '+%Y-%m-%d %H:%M:%S')] 加载: $_TAR" >> "$_BG_LOG"
                                if docker load -i "$REMOTE_IMAGE_DIR/$_TAR" >> "$_BG_LOG" 2>&1; then
                                    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ✅ 已加载: $_IMG" >> "$_BG_LOG"
                                else
                                    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ❌ docker load 失败: $_TAR" >> "$_BG_LOG"
                                fi
                            elif [[ "${KEEP_TAR:-false}" == "true" ]]; then
                                # 需要保留 tar：用 rsync 断点续传后 load
                                _LOCAL_TAR="$_DOWNLOAD_DIR/$_TAR"
                                _LOCAL_TAR_TMP="${_LOCAL_TAR}.tmp"
                                echo "[$(date '+%Y-%m-%d %H:%M:%S')] 下载(rsync): $_TAR" >> "$_BG_LOG"
                                rm -f "$_LOCAL_TAR_TMP"
                                if ! _rsync "$REMOTE_HOST:$REMOTE_IMAGE_DIR/$_TAR" "$_LOCAL_TAR_TMP" >> "$_BG_LOG" 2>&1; then
                                    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ❌ 下载失败: $_TAR，跳过。" >> "$_BG_LOG"
                                    rm -f "$_LOCAL_TAR_TMP"
                                    continue
                                fi
                                mv "$_LOCAL_TAR_TMP" "$_LOCAL_TAR"
                                echo "[$(date '+%Y-%m-%d %H:%M:%S')] 加载: $_TAR" >> "$_BG_LOG"
                                if docker load -i "$_LOCAL_TAR" >> "$_BG_LOG" 2>&1; then
                                    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ✅ 已加载: $_IMG" >> "$_BG_LOG"
                                else
                                    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ❌ docker load 失败: $_TAR" >> "$_BG_LOG"
                                fi
                            else
                                # 默认：SSH 管道流式传输，不落盘
                                echo "[$(date '+%Y-%m-%d %H:%M:%S')] 流式传输并加载: $_TAR" >> "$_BG_LOG"
                                if _ssh "$REMOTE_HOST" "cat '$REMOTE_IMAGE_DIR/$_TAR'" 2>>"$_BG_LOG" | docker load >> "$_BG_LOG" 2>&1; then
                                    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ✅ 已加载: $_IMG" >> "$_BG_LOG"
                                else
                                    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ❌ 流式加载失败: $_TAR" >> "$_BG_LOG"
                                fi
                            fi
                        done <<< "$_MISSING"
                        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 后台镜像更新完成" >> "$_BG_LOG"
                    ) &
                    echo "⏳ 后台更新已启动 (PID $!)，日志: $_BG_LOG"
                else
                    echo "⏭️  跳过镜像更新。"
                fi
            fi
        fi
    fi
    echo "----------------------------------------"
fi

# 4. 获取系统中所有的 Docker Images (排除悬空镜像 <none>)
echo "正在获取 Docker 镜像列表..."
mapfile -t images < <(docker images --format "{{.Repository}}:{{.Tag}}" | grep -v "<none>")
image_count=${#images[@]}

if [ "$image_count" -eq 0 ]; then
    echo "❌ 未在系统中找到任何 Docker 镜像！"
    read -p "按回车键退出..."
    exit 1
elif [ "$image_count" -eq 1 ]; then
    SELECTED_IMAGE="${images[0]}"
    echo "✅ 系统中只有一个镜像，自动选择: $SELECTED_IMAGE"
else
    echo "🔍 发现多个 Docker 镜像，请选择要运行的镜像:"
    PS3="请输入要使用的镜像编号: "
    select img in "${images[@]}"; do
        if [ -n "$img" ]; then
            SELECTED_IMAGE=$img
            echo "已选择: $SELECTED_IMAGE"
            break
        else
            echo "❌ 无效的输入，请重新输入编号。"
        fi
    done
fi

echo "----------------------------------------"
echo "🚀 正在进入 Docker 环境..."
echo "----------------------------------------"

# 5. 执行 Docker 运行命令
docker run -it --rm -w /root/automated_tests -e DISPLAY=$DISPLAY -v /tmp/.X11-unix:/tmp/.X11-unix -v "${HOST_PATH}:${CONTAINER_PATH}" "${SELECTED_IMAGE}" bash -c "source ./device/set_env.sh && exec bash"

echo "----------------------------------------"
echo "   Docker 环境已退出。"
read -p "按回车键关闭窗口..."
