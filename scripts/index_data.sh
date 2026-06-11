#!/bin/bash
# ============================================================
# Pipeline 单仓数据导入脚本
#
# 用法:
#   ./scripts/index_data.sh run -d /path/to/repo [-o ./data] [--clear false]
#   ./scripts/index_data.sh run_bg -d /path/to/repo [-o ./data]
#
# 参数:
#   run       直接执行（默认）
#   run_bg    后台执行，日志输出到 logs/index_<repo>.log
#   -d        源码目录（必填）
#   -o        数据存储根目录（默认 ./data）
#   --clear   是否清空旧数据重导（默认 true）
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

MODE="run"
SOURCE_DIR=""
DATA_DIR="./data"
CLEAR="true"

while [[ $# -gt 0 ]]; do
    case $1 in
        run|run_bg)
            MODE="$1"; shift ;;
        -d)
            SOURCE_DIR="$2"; shift 2 ;;
        -o)
            DATA_DIR="$2"; shift 2 ;;
        --clear)
            CLEAR="$2"; shift 2 ;;
        *)
            echo "未知参数: $1"; exit 1 ;;
    esac
done

if [ -z "$SOURCE_DIR" ]; then
    echo "错误: 缺少 -d 参数（源码目录）"
    echo "用法: $0 run -d /path/to/repo [-o ./data] [--clear false]"
    exit 1
fi

SOURCE_DIR="$(cd "$SOURCE_DIR" 2>/dev/null && pwd || echo "$SOURCE_DIR")"
REPO_NAME="$(basename "$SOURCE_DIR")"
DATA_DIR="$(cd "$DATA_DIR" 2>/dev/null && pwd || echo "$DATA_DIR")"

cd "$PROJECT_DIR"

# ── 清空旧数据 ──
if [ "$CLEAR" = "true" ]; then
    REPO_DATA="$DATA_DIR/repos/$REPO_NAME"
    if [ -d "$REPO_DATA" ]; then
        echo "清空旧数据: $REPO_DATA"
        rm -rf "$REPO_DATA"
    fi
else
    echo "增量模式: 保留已有数据，仅追加新增/变更文件"
fi

export PIPELINE_DATA_DIR="$DATA_DIR"
export PIPELINE_CLEAR="$CLEAR"

PY_CMD="python -m pipeline.orchestrator --repo \"$REPO_NAME\" --data-dir \"$DATA_DIR\" \"$SOURCE_DIR\""

case $MODE in
    run)
        echo "开始导入: repo=$REPO_NAME source=$SOURCE_DIR data=$DATA_DIR clear=$CLEAR"
        eval "$PY_CMD"
        echo "导入完成: $REPO_NAME"
        ;;
    run_bg)
        LOG_FILE="$PROJECT_DIR/logs/index_${REPO_NAME}.log"
        mkdir -p "$PROJECT_DIR/logs"
        echo "后台导入: repo=$REPO_NAME source=$SOURCE_DIR data=$DATA_DIR clear=$CLEAR"
        echo "日志文件: $LOG_FILE"
        nohup bash -c "$PY_CMD" > "$LOG_FILE" 2>&1 &
        echo "PID: $!"
        ;;
esac
