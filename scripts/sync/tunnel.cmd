@echo off
REM Idea Hub 云端 Web 访问隧道（Windows）
REM 用法: tunnel.cmd <user> <server-ip> [port]
REM 效果: 建立 SSH 隧道后，浏览器打开 http://127.0.0.1:8000 即可访问云端 Idea Hub

set USER=%1
set SERVER=%2
set PORT=%~3
if "%PORT%"=="" set PORT=22

echo 正在建立 SSH 隧道 %USER%@%SERVER% ... 
echo 隧道建立后请打开浏览器访问 http://127.0.0.1:8000
echo 按 Ctrl+C 关闭隧道

ssh -N -L 8000:127.0.0.1:8000 -p %PORT% %USER%@%SERVER%
