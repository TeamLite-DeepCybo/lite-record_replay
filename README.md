# Lite 动作录制 / 回放

Prime Lite 半身双臂机器人 —— 拖动示教录制、按真实速度回放。

```bash
./install.sh    # 一键安装（新机器一次）
./start.sh      # 一键启动
```

启动时会自动配置 CAN 口、自动识别左右臂所在总线、有没有夹爪、哪些电机在线，
扫完直接在界面上点「开始录制」即可，不需要改任何代码。

## 版本

| 版本 | 内容 |
|---|---|
| v1.0.0 | 原始录制/回放（上游 [Prime-Lite](https://github.com/pengyichen2026/Prime-Lite/tree/main/record_replay)） |
| v1.1.0 | 加入 CAN 通道自动识别、在线电机降级 |
| v1.2.0 | 加入重复回放 |
| v2.0.0 | 重构：单份代码 + `app/bhl` 模块化 + 命令行参数（本次） |

## 目录

```
app/            程序本体（入口 main.py，模块 bhl/）
start.sh        一键启动
install.sh      一键安装
```
