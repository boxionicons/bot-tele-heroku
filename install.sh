#!/bin/bash

trap 'echo "Terjadi kesalahan. Periksa log di atas untuk detailnya."' ERR

APP_NAME="tambo-cornel-pks"
USER_ID="7232481494"
BOT_TOKEN="7688353764:AAFhwokBUgvuHU245NqZmO16gCcV_WoHmzE"
API_ID="24304592"
API_HASH="8f67c47ceaf6e5fcb197d0841b0443e9"
DEVICE_MODEL="IPhone"
TIMEZONE="Asia/Jakarta"
SECRET_KEY="243045928f67c47ceaf6e5fcb197d0841b0443e9"
PORT="8080"

log_file="deployment.log"
echo "Log file: $log_file"
echo "" > $log_file  # Clear log file

echo "Membuat bot : $APP_NAME..."
if ! heroku create $APP_NAME >> $log_file 2>&1; then
  echo "Berhasil membuat aplikasi Heroku. Lihat log untuk detailnya."
else
  echo "Gagal membuat aplikasi Heroku. Lihat log untuk detailnya."
fi

if ! heroku addons:create heroku-postgresql -a $APP_NAME >> $log_file 2>&1; then
  echo "Berhasil menambahkan database PostgreSQL. Lihat log untuk detailnya."
else
  echo "Gagal menambahkan database PostgreSQL. Lihat log untuk detailnya."
fi

echo "Setting environment variables..."
if ! heroku config:set USER_ID=$USER_ID \
  BOT_TOKEN=$BOT_TOKEN \
  SECRET_KEY=$SECRET_KEY \
  API_ID=$API_ID \
  API_HASH=$API_HASH \
  DEVICE_MODEL=$DEVICE_MODEL \
  SYSTEM_VERSION=1.0 \
  TIMEZONE=$TIMEZONE \
  DEVICE_LANGUAGE_CODE=en \
  PORT=$PORT \
  -a $APP_NAME >> $log_file 2>&1; then
  echo "Berhasil mengatur variabel lingkungan. Lihat log untuk detailnya."
else
  echo "Gagal mengatur variabel lingkungan. Lihat log untuk detailnya."
fi

echo "Initializing Git repository and deploying to Heroku..."
if ! git init >> $log_file 2>&1; then
  echo "Gagal menginisialisasi Git repository. Lihat log untuk detailnya."
else
  echo "Berhasil menginisialisasi Git repository. Lihat log untuk detailnya."
fi

if ! heroku git:remote -a $APP_NAME >> $log_file 2>&1; then
  echo "Berhasil menambahkan remote Heroku. Lihat log untuk detailnya."
else
  echo "Gagal menambahkan remote Heroku. Lihat log untuk detailnya."
fi

if ! git add . >> $log_file 2>&1; then
  echo "Gagal menambahkan file ke Git. Lihat log untuk detailnya."
else
  echo "Berhasil menambahkan file ke Git. Lihat log untuk detailnya."
fi

if ! git commit -m "Initial Commit" >> $log_file 2>&1; then
  echo "Gagal membuat commit Git. Lihat log untuk detailnya."
else
  echo "Berhasil membuat commit Git. Lihat log untuk detailnya."
fi

if ! git push heroku master >> $log_file 2>&1; then
  echo "Gagal mendorong kode ke Heroku. Lihat log untuk detailnya."
else
  echo "Berhasil mendorong kode ke Heroku. Lihat log untuk detailnya."
fi

echo "Deployment complete! Lihat $log_file untuk detail log."