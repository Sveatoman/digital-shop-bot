@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .env (
    echo Файл .env не найден. Скопируйте .env.example в .env и заполните токены!
    copy .env.example .env
    pause
    exit /b 1
)
python bot.py
pause
