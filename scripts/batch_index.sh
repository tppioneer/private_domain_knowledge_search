#!/bin/bash
# ============================================================
# Pipeline 批量数据导入脚本（遍历子目录，每个子目录为一个 repo）
#
# 用法:
#   ./scripts/batch_index.sh -d /path/to/repos -o ./data
#
# 参数:
#   -d       父目录（遍历其下子目录，每个子目录作为一个 repo）
#   -o       数据存储根目录（默认 ./data）
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SOURCE_PARENT=""
DATA_DIR="./data"

while [[ $# -gt 0 ]]; do
    case $1 in
        -d)
            SOURCE_PARENT="$2"
            shift 2
            ;;
        -o)
            DATA_DIR="$2"
            shift 2
            ;;
        *)
            echo "未知参数: $1"
            echo "用法: $0 -d /path/to/repos [-o ./data]"
            exit 1
            ;;
    esac
done

if [ -z "$SOURCE_PARENT" ]; then
    echo "错误: 缺少 -d 参数（父目录）"
    exit 1
fi

echo "批量导入: 遍历 $SOURCE_PARENT 下的子目录..."

count=0
for subdir in "$SOURCE_PARENT"/*/; do
    subdir="${subdir%/}"  # 去掉末尾 /
    if [ -d "$subdir" ]; then
        name="$(basename "$subdir")"
        echo ""
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "[$((count + 1))] 导入: $name ($subdir)"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        bash "$SCRIPT_DIR/index_data.sh" run -d "$subdir" -o "$DATA_DIR" -c true
        count=$((count + 1))
    fi
done

echo ""
echo "批量导入完成: $count 个仓库"
