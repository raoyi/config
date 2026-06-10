import streamlit as st
import requests
import subprocess
import os
import time
import csv
import re
import socket
from concurrent.futures import ThreadPoolExecutor

# Windows: streamlit run .\app.py
# Mac/Linux/WSL: streamlit run app.py
# sudo apt install python3-pip -y
# pip install streamlit requests --break-system-packages

# ================= 核心配置区 =================
NODE_LIST_FILE = "nodes.csv"             # 节点配置文件
SSH_USER = "qauser"                      # 树莓派 SSH 用户名
CONTAINER_NAME = "qa-bot"                # 您的容器名称
IMAGE_NAME = "my-app:latest"             # 您的镜像名称
API_PORT = 80                            # 树莓派 API 端口
_TAR_CANDIDATES = ["./qabot_runner.tar.gz", "./qabot_runner.tar"]
FIXED_TAR_PATH = next((p for p in _TAR_CANDIDATES if os.path.exists(p)), _TAR_CANDIDATES[0])
# ============================================

# st.set_page_config(page_title="树莓派集群管理台", page_icon="🍓", layout="wide")
st.set_page_config(page_title="树莓派集群管理台", layout="wide")

def _get_local_subnet():
    """获取本机所在子网，如 192.168.31.0/24"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        prefix = ".".join(local_ip.split(".")[:3])
        return prefix
    except Exception:
        return None

def _probe_host(ip):
    """用 TCP socket 探测 IP，触发系统 ARP 缓存更新"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.15)
            s.connect((ip, 80))
    except OSError:
        pass

def _parse_arp() -> dict:
    """读取 ARP 表，返回 {mac_lower: ip}"""
    mac_to_ip = {}
    try:
        result = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=5)
        for line in result.stdout.splitlines():
            ip_match = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", line)
            mac_match = re.search(r"([0-9a-fA-F]{2}[:\-][0-9a-fA-F]{2}[:\-][0-9a-fA-F]{2}[:\-][0-9a-fA-F]{2}[:\-][0-9a-fA-F]{2}[:\-][0-9a-fA-F]{2})", line)
            if ip_match and mac_match:
                mac = mac_match.group(1).replace("-", ":").lower()
                mac_to_ip[mac] = ip_match.group(1)
    except Exception:
        pass
    return mac_to_ip

def get_arp_table(target_macs: list) -> dict:
    """先读 ARP 表，若有目标 MAC 未找到则扫描子网后再读一次"""
    mac_to_ip = _parse_arp()
    missing = [m for m in target_macs if m not in mac_to_ip]
    if missing:
        prefix = _get_local_subnet()
        if prefix:
            targets = [f"{prefix}.{i}" for i in range(1, 255)]
            with ThreadPoolExecutor(max_workers=200) as pool:
                pool.map(_probe_host, targets)
        mac_to_ip = _parse_arp()
    return mac_to_ip

def load_nodes():
    """解析 CSV 文件加载节点信息，CSV 格式: MAC地址, 别名"""
    nodes = []
    if not os.path.exists(NODE_LIST_FILE):
        return nodes
    rows = []
    with open(NODE_LIST_FILE, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row or str(row[0]).strip().startswith("#"):
                continue
            mac = str(row[0]).strip().lower()
            alias = str(row[1]).strip() if len(row) > 1 and str(row[1]).strip() else "未命名设备"
            if mac:
                rows.append((mac, alias))
    target_macs = [r[0] for r in rows]
    arp_table = get_arp_table(target_macs)
    for mac, alias in rows:
        nodes.append({"ip": arp_table.get(mac), "mac": mac, "alias": alias})
    return nodes

def get_node_status(ip):
    """通过 API 获取节点在线状态与版本"""
    try:
        url = f"http://{ip}:{API_PORT}/v1/system"
        response = requests.get(url, timeout=2)
        if response.status_code == 200:
            data = response.json()
            return {"status": "🟢 在线", "version": data.get("version", 0)}
    except Exception:
        pass
    return {"status": "🔴 离线", "version": 0}

def execute_update(ip, tar_file_path):
    """通过 SSH 和 SCP 执行更新逻辑 (针对固定大文件优化)"""
    try:
        # 1. 传输大文件
        scp_cmd = ["scp", "-q", tar_file_path, f"{SSH_USER}@{ip}:/tmp/update.tar.gz"]
        subprocess.run(scp_cmd, check=True)
        
        # 2. 远程执行 Docker 替换及清理命令
        ssh_cmd = [
            "ssh", "-q", f"{SSH_USER}@{ip}",
            f"""
            sudo docker load -i /tmp/update.tar.gz && \
            sudo docker rm -f {CONTAINER_NAME} && \
            sudo docker run -d --name {CONTAINER_NAME} --restart always -p 80:80 {IMAGE_NAME} && \
            rm -f /tmp/update.tar.gz && \
            sudo docker image prune -f
            """
        ]
        subprocess.run(ssh_cmd, check=True)
        return True
    except subprocess.CalledProcessError as e:
        st.error(f"传输或执行时发生错误: {e}")
        return False

# ================= UI 构建 =================

st.title("🍓 树莓派 Docker 集群控制台")

# 检查本地固定包是否存在
tar_exists = os.path.exists(FIXED_TAR_PATH)

# 侧边栏：全局控制
with st.sidebar:
    st.header("⚙️ 发布新版本")
    
    # 彻底解决加减按钮：使用纯文本输入，后台校验转数字
    target_version_str = st.text_input("输入目标版本号 (纯数字)", value="21690")
    if target_version_str.isdigit():
        target_version = int(target_version_str)
    else:
        st.error("⚠️ 请输入有效的纯数字版本号！")
        target_version = 0  # 设为无效值，防止错误触发更新
    
    st.markdown("---")
    st.header("📦 镜像包检测")
    if tar_exists:
        # 计算文件大小并友好显示
        size_gb = os.path.getsize(FIXED_TAR_PATH) / (1024 ** 3)
        st.success(f"🟢 已检测到本地镜像文件\n\n**文件名**: `{os.path.basename(FIXED_TAR_PATH)}`\n\n**大小**: `{size_gb:.2f} GB`")
    else:
        st.error(f"🔴 未检测到本地镜像文件！\n\n请确保工作目录下存在名为 `{os.path.basename(FIXED_TAR_PATH)}` 的文件。")
    
    st.markdown("---")
    if st.button("🔄 刷新集群状态", use_container_width=True):
        st.rerun()

# 主界面：节点列表
subnet = _get_local_subnet()
with st.spinner(f"正在扫描 {subnet}.0/24，获取树莓派 IP..."):
    nodes = load_nodes()

with st.expander("🔍 调试信息（确认后可删除）"):
    st.write(f"**本机检测到的子网前缀：** `{subnet}`")
    arp_raw = subprocess.run(["arp", "-a"], capture_output=True, text=True).stdout
    st.text("ARP 表原始输出：")
    st.code(arp_raw)
    st.write("**解析到的 MAC→IP 映射：**")
    st.json(_parse_arp())

if not nodes:
    st.warning(f"找不到节点配置，请在控制端创建 `{NODE_LIST_FILE}` CSV文件。")
else:
    st.subheader("🌐 节点状态列表")
    
    # 建立表头
    c_alias, c_ip, c_mac, c_status, c_ver, c_action = st.columns([2.5, 1.8, 2, 1.2, 1.3, 2.5])
    c_alias.markdown("**🏷️ 设备别名**")
    c_ip.markdown("**🖥️ IP 地址**")
    c_mac.markdown("**🏷️ MAC 地址**")
    c_status.markdown("**📶 状态**")
    c_ver.markdown("**📦 当前版本**")
    c_action.markdown("**⚙️ 操作**")
    st.markdown("---")
    
    # 并发获取所有节点状态
    def _fetch(node):
        ip = node["ip"]
        return get_node_status(ip) if ip else {"status": "⚫ 未找到", "version": 0}

    with ThreadPoolExecutor(max_workers=len(nodes)) as pool:
        statuses = list(pool.map(_fetch, nodes))

    # 遍历每个节点渲染行
    for node, info in zip(nodes, statuses):
        ip = node["ip"]
        mac = node["mac"]
        alias = node["alias"]

        c_alias, c_ip, c_mac, c_status, c_ver, c_action = st.columns([2.5, 1.8, 2, 1.2, 1.3, 2.5])

        c_alias.write(f"**{alias}**")
        c_ip.code(ip if ip else "—")
        c_mac.code(mac)
        c_status.write(info["status"])
        c_ver.write(f"`{info['version']}`")

        with c_action:
            # 按钮激活条件：IP 已解析、设备在线、本地固定镜像包存在、版本输入合法
            btn_disabled = (ip is None) or (info["version"] == 0) or (not tar_exists) or (target_version == 0)

            # 如果当前版本低于目标版本，显示部署按钮
            if ip and info["version"] > 0 and info["version"] < target_version:
                if st.button(f"🚀 部署至此节点", key=f"btn_{mac}", disabled=btn_disabled):
                    with st.status(f"正在更新 {alias}...", expanded=True) as status:
                        st.write("1. 正在通过 SCP 传输镜像包...")
                        success = execute_update(ip, FIXED_TAR_PATH)
                        if success:
                            status.update(label="部署成功！即将刷新状态", state="complete", expanded=False)
                            time.sleep(1.5)
                            st.rerun()
                        else:
                            status.update(label="部署失败，请检查连接", state="error")
            elif ip and info["version"] >= target_version and target_version > 0:
                st.write("✅ 已是最新版")
            else:
                st.write("➖ 无法操作")
