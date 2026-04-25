"""Locust 压力测试脚本

测试场景:
  - 简单查询: 审批查询、日程查询等单系统操作
  - 中等复杂: 跨系统查询，如审批并通知
  - 复杂任务: 多步推理，如整理审批并批量处理

性能目标:
  | 场景     | 并发数 | 持续时间 | 目标 QPS | P95 延迟 | 错误率 |
  |---------|--------|---------|---------|---------|--------|
  | 简单查询 | 500    | 10min   | 200     | < 3s    | < 1%   |
  | 混合场景 | 1000   | 10min   | 500     | < 5s    | < 2%   |
  | 峰值压测 | 2000   | 5min    | 800     | < 10s   | < 5%   |

使用方法:
  locust -f tests/performance/locustfile.py --host=https://agent.company.com
  locust -f tests/performance/locustfile.py --host=http://localhost:8000
"""

import json
import random
import time

from locust import HttpUser, task, between, events


class AgentPlatformUser(HttpUser):
    """模拟企业办公平台用户"""

    wait_time = between(1, 3)

    # 测试用户池
    TEST_USERS = [f"perf_test_user_{i:03d}" for i in range(1, 101)]

    # 简单查询消息池
    SIMPLE_QUERIES = [
        "查看我的待审批列表",
        "今天有什么会议",
        "查看我的邮件",
        "查询我的考勤记录",
        "查看我的假期余额",
        "查询本周日程安排",
        "查看最近的报销记录",
        "搜索关于年假制度的文档",
    ]

    # 中等复杂消息池
    MEDIUM_QUERIES = [
        "帮我查看待审批列表并通知申请人",
        "查看今天的会议安排，如果有冲突帮我调整",
        "查询我的报销进度，如果被驳回告诉我原因",
        "查看我的考勤异常记录并说明处理方式",
        "查询客户信息并整理成摘要",
    ]

    # 复杂任务消息池
    COMPLEX_QUERIES = [
        "整理本周所有待审批事项，按优先级排序，批量处理高优先级审批，并给每个申请人发送通知邮件",
        "查看今天所有会议，整理会议纪要模板，发送给参会人确认",
        "汇总本月报销情况，按类别统计金额，生成报销摘要报告",
    ]

    def on_start(self):
        """用户初始化"""
        self.user_id = random.choice(self.TEST_USERS)
        self.session_id = None

    @task(5)
    def simple_query(self):
        """简单查询任务（权重最高）"""
        message = random.choice(self.SIMPLE_QUERIES)
        self._send_chat_request(message)

    @task(3)
    def medium_query(self):
        """中等复杂任务"""
        message = random.choice(self.MEDIUM_QUERIES)
        self._send_chat_request(message)

    @task(1)
    def complex_task(self):
        """复杂多步任务"""
        message = random.choice(self.COMPLEX_QUERIES)
        self._send_chat_request(message)

    @task(2)
    def health_check(self):
        """健康检查"""
        self.client.get("/api/v1/admin/health", name="/health")

    def _send_chat_request(self, message: str):
        """发送对话请求"""
        payload = {
            "user_id": self.user_id,
            "message": message,
            "session_id": self.session_id,
            "channel": "web",
        }

        with self.client.post(
            "/api/v1/agent/chat",
            json=payload,
            name="/agent/chat",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                data = response.json()
                if data.get("session_id"):
                    self.session_id = data["session_id"]
                if data.get("message"):
                    response.success()
                else:
                    response.failure("响应缺少 message 字段")
            elif response.status_code == 429:
                response.failure("限流: 请求过于频繁")
            else:
                response.failure(f"HTTP {response.status_code}")


class StreamUser(HttpUser):
    """模拟流式对话用户"""

    wait_time = between(2, 5)

    TEST_USERS = [f"stream_user_{i:03d}" for i in range(1, 51)]

    SIMPLE_QUERIES = [
        "查看我的待审批列表",
        "今天有什么会议",
        "查询我的考勤记录",
    ]

    def on_start(self):
        self.user_id = random.choice(self.TEST_USERS)
        self.session_id = None

    @task(1)
    def stream_chat(self):
        """流式对话请求"""
        message = random.choice(self.SIMPLE_QUERIES)
        payload = {
            "user_id": self.user_id,
            "message": message,
            "session_id": self.session_id,
            "channel": "web",
        }

        with self.client.post(
            "/api/v1/agent/chat/stream",
            json=payload,
            name="/agent/chat/stream",
            catch_response=True,
            stream=True,
        ) as response:
            if response.status_code == 200:
                chunks_received = 0
                try:
                    for line in response.iter_lines():
                        if line:
                            chunks_received += 1
                            if b'"event": "session_id"' in line:
                                try:
                                    data = json.loads(line.decode().replace("data: ", ""))
                                    if data.get("data"):
                                        self.session_id = data["data"]
                                except (json.JSONDecodeError, KeyError):
                                    pass
                    if chunks_received > 0:
                        response.success()
                    else:
                        response.failure("未收到任何 SSE 事件")
                except Exception as e:
                    response.failure(f"流式读取异常: {e}")
            else:
                response.failure(f"HTTP {response.status_code}")


# ==================== 测试事件监听 ====================

@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """测试结束时的汇总"""
    stats = environment.stats
    print("\n" + "=" * 60)
    print("压力测试结果汇总")
    print("=" * 60)
    print(f"总请求数: {stats.total.num_requests}")
    print(f"总失败数: {stats.total.num_failures}")
    print(f"平均响应时间: {stats.total.avg_response_time:.2f}ms")
    print(f"P50 响应时间: {stats.total.get_response_time_percentile(0.5):.2f}ms")
    print(f"P95 响应时间: {stats.total.get_response_time_percentile(0.95):.2f}ms")
    print(f"P99 响应时间: {stats.total.get_response_time_percentile(0.99):.2f}ms")
    print(f"RPS: {stats.total.total_rps:.2f}")
    print(f"错误率: {stats.total.fail_ratio:.2%}")
    print("=" * 60)
