  ---
  工作流程（5 步）：
  Step 1  读取 device.json          → 解析 ip_address
  Step 2  连接 DUT 采集信息          → GET /query/device-info（公开 ECP）
                                       GET /query/device/secret（私有 ECP）
  Step 3  预览写入字段               → 显示将写入的 status_id / version / comment
  Step 4  查询 TestRail             → 获取目标 run/plan/milestone 下的所有 case_id
  Step 5  写入结果（批量 250 条/次） → add_results_for_cases，失败自动逐条回退

  从 DUT 自动采集的字段：

  ┌───────────────────────┬──────────────────────┬──────────────────────────────────────────────┐
  │     TestRail 字段     │       ECP 来源       │                   XML 标签                   │
  ├───────────────────────┼──────────────────────┼──────────────────────────────────────────────┤
  │ version               │ /query/device-info   │ build-number（已是 TeamCity 格式）           │
  ├───────────────────────┼──────────────────────┼──────────────────────────────────────────────┤
  │ comment: ESN          │ /query/device-info   │ serial-number                                │
  ├───────────────────────┼──────────────────────┼──────────────────────────────────────────────┤
  │ comment: Device ID    │ /query/device-info   │ device-id                                    │
  ├───────────────────────┼──────────────────────┼──────────────────────────────────────────────┤
  │ comment: Roku Model   │ /query/device-info   │ model-number                                 │
  ├───────────────────────┼──────────────────────┼──────────────────────────────────────────────┤
  │ comment: Device Info  │ /query/device-info   │ software-version + vendor-name + screen-size │
  ├───────────────────────┼──────────────────────┼──────────────────────────────────────────────┤
  │ comment: Model Region │ /query/device/secret │ modelRegion                                  │
  └───────────────────────┴──────────────────────┴──────────────────────────────────────────────┘

  elapsed 一律不传（TestRail 收到无此字段即留空）。

=================================================================================

用法：
# 对 /tests/view/12187090 这条写 Passed
  python mark_passed.py -d device.json --testrail-run 239549 --test-ids 12187090
# 多条
  python mark_passed.py -d device.json --testrail-run 239549 --test-ids 12187090,12187091,12187092
  
  
~/automated_tests$ python ./device/mark_passed.py -d ./device/device-box-HS300.json --testrail-plan 249861 --test-ids 13729937
Traceback (most recent call last):
  File "/root/automated_tests/./device/mark_passed.py", line 43, in <module>
    from roku_common.testrail_client import TestRailClient as _FrameworkTRClient
  File "/root/roku_automation/roku_common/testrail_client.py", line 27, in <module>
    from roku_common.jira_client import JIRA_BUGS_URL
  File "/root/roku_automation/roku_common/jira_client.py", line 122, in <module>
    class JIRAClient:
    ...<730 lines>...
            return [{"name": component.name, "id": component.id} for component in project_components]
  File "/root/roku_automation/roku_common/jira_client.py", line 149, in JIRAClient
    username: str = cast("str", os.environ.get("ATLASSIAN_USER") or get_config_key("atlassian_user")),
                                                                    ~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^
  File "/root/roku_automation/roku_common/__init__.py", line 107, in get_config_key
    config.read(user_config.USER_CONFIG_FILE)
    ~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/configparser.py", line 737, in read
    self._read(fp, filename)
    ~~~~~~~~~~^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/configparser.py", line 1052, in _read
    ParsingError._raise_all(self._read_inner(fp, fpname))
                            ~~~~~~~~~~~~~~~~^^^^^^^^^^^^
  File "/usr/lib/python3.13/configparser.py", line 1081, in _read_inner
    self._handle_rest(st, line, fpname)
    ~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/configparser.py", line 1105, in _handle_rest
    raise MissingSectionHeaderError(fpname, st.lineno, line)
configparser.MissingSectionHeaderError: File contains no section headers.
file: PosixPath('/root/.ra_config'), line: 1
'{\n'







13729971,13729986,13729992,13729993,13729994,13729995,13729997,13729980,13729981,13729982,13729983,13729984,13729977,13729988,13729978,13729979,13729998,13729999,13729989,13729990,13730016,13730021,13730017,13730018,13730019,13730036,13730024,13730025,13730026,13730027,13730028,13730031,13730032,13730035,13730037,13730042,13730043,13730045,13730029,13730030,13730033,13730044,13730034,13730038,13730039,13730040,13730041,13730055,13730056
