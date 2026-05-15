#!/bin/bash

# 1. 检查当前目录下是否存在 .env 文件
if [ ! -f ".env" ]; then
    cp .env.example .env
fi
echo "=> 发现 .env 文件，正在合并环境变量..."

set -a
source .env
set +a
echo "=> 环境变量加载完成！"
echo "=> 开始运行 docker build . ..."
docker build -t $HERMES_IMAGE .