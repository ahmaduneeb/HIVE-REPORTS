@echo off
cd /d "%~dp0"
start "" C:\Users\USER\AppData\Local\Programs\Python\Python311\python.exe -m hive_reports.gui %*
