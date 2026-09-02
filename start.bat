@echo off
rem 三角洲封禁倒计时 - 后台启动脚本
rem 放到 Windows 启动文件夹(shell:startup)或计划任务即可开机常驻
cd /d %~dp0
start /min "" pythonw main.py --web