# 幂等处理最佳实践

## 概述

使用 bizId+uid+eventType 作为幂等键，调用前检查流水是否已存在。
