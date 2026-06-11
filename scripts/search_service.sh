#!/bin/bash
# ============================================================
# Search Service 生命周期管理
#
# 用法:
#   ./scripts/search_service.sh start    [--port 8080] [--host 0.0.0.0]
#   ./scripts/search_service.sh stop
#   ./scripts/search_service.sh restart  [--port 8080]
#
# 默认: start
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

ACTION="${1:-start}"
HOST="0.0.0.0"
PORT="8080"
DATA_DIR="${DATA_DIR:-./data}"

# --port / --host / --data-dir 解析
while [[ $# -gt 0 ]]; do
    case $1 in
        --port)
            PORT="$2"
            shift 2
            ;;
        --host)
            HOST="$2"
            shift 2
            ;;
        --data-dir)
            DATA_DIR="$2"
            shift 2
            ;;
        start|stop|restart)
            ACTION="$1"
            shift
            ;;
        *)
            shift
            ;;
    esac
done

PID_FILE="$PROJECT_DIR/logs/search_service.pid"
LOG_FILE="$PROJECT_DIR/logs/search_service.log"
ACCESS_LOG="$PROJECT_DIR/logs/access.log"

cd "$PROJECT_DIR"
mkdir -p logs

_get_pid() {
    if [ -f "$PID_FILE" ]; then
        cat "$PID_FILE"
    fi
}

_is_running() {
    local pid=$(_get_pid)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        return 0
    fi
    return 1
}

_start() {
    if _is_running; then
        echo "Search Service 已在运行 (PID: $(_get_pid))"
        return 0
    fi

    echo "启动 Search Service: host=$HOST port=$PORT data=$DATA_DIR"

    # 设置数据路径环境变量
    export SQLITE_DB_PATH="${DATA_DIR}/knowledge.db"
    export FAISS_INDEX_DIR="${DATA_DIR}/faiss"
    export GRAPH_STORAGE_PATH="${DATA_DIR}/graph.json"
    export REPO_DATA_DIR="${DATA_DIR}/repos"

    nohup python -m uvicorn search_service.main:app \
        --host "$HOST" --port "$PORT" \
        > "$LOG_FILE" 2>&1 &
    local pid=$!
    echo "$pid" > "$PID_FILE"
    sleep 2

    if _is_running; then
        echo "Search Service 启动成功 (PID: $pid)"
        echo "  API:     http://${HOST}:${PORT}/api/v1/search"
        echo "  Health:  http://${HOST}:${PORT}/health"
        echo "  Repos:   http://${HOST}:${PORT}/api/v1/repos"
        echo "  日志:    $LOG_FILE"
    else
        echo "启动失败，检查日志: $LOG_FILE"
        tail -5 "$LOG_FILE"
        rm -f "$PID_FILE"
        exit 1
    fi
}

_stop() {
    local pid=$(_get_pid)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        echo "停止 Search Service (PID: $pid)..."
        kill "$pid"
        sleep 2
        if kill -0 "$pid" 2>/dev/null; then
            echo "强制终止..."
            kill -9 "$pid"
        fi
        rm -f "$PID_FILE"
        echo "Search Service 已停止"
    else
        echo "Search Service 未运行"
        rm -f "$PID_FILE"
    fi
}

case $ACTION in
    start)
        _start
        ;;
    stop)
        _stop
        ;;
    restart)
        _stop
        sleep 1
        _start
        ;;
    *)
        echo "用法: $0 {start|stop|restart} [--port 8080] [--host 0.0.0.0]"
        exit 1
        ;;
esac
