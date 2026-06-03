#!/bin/bash

# 1. 检查当前目录下是否存在 .env 文件
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "=> 未找到 .env 文件，已从 .env.example 模版生成"
else
    echo "=> 发现本地 .env 文件"
fi

set -a
source .env
set +a
echo "=> 环境变量加载完成！"

# 2. 将本地 .env 复制到 ~/.hermes/.env（若尚不存在），
#    使容器启动时 stage2-hook.sh 的 seed_one 不再用镜像模版覆盖
HERMES_DATA_DIR="${HERMES_HOME:-$HOME/.hermes}"
mkdir -p "$HERMES_DATA_DIR"
if [ ! -f "$HERMES_DATA_DIR/.env" ]; then
    cp .env "$HERMES_DATA_DIR/.env"
    chmod 600 "$HERMES_DATA_DIR/.env"
    echo "=> 已将 .env 复制到 $HERMES_DATA_DIR/.env"
fi

echo "=> 开始运行 docker build . ..."
docker build -t $HERMES_IMAGE .