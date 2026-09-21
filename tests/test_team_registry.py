"""AgentNameRegistry 单测。"""

from __future__ import annotations

from suisuicode.team.registry import AgentNameRegistry


class TestRegister:
    """注册与解析基本流程。"""

    def test_register_and_resolve(self) -> None:
        reg = AgentNameRegistry()
        reg.register("alice", "agent-123")
        assert reg.resolve("alice") == "agent-123"
        assert reg.name_of("agent-123") == "alice"

    def test_resolve_by_id(self) -> None:
        reg = AgentNameRegistry()
        reg.register("bob", "agent-456")
        assert reg.resolve("agent-456") == "agent-456"

    def test_resolve_unknown(self) -> None:
        reg = AgentNameRegistry()
        assert reg.resolve("nobody") is None
        assert reg.name_of("agent-xxx") is None

    def test_overwrite_name(self) -> None:
        """同名覆盖：后注册的 agent_id 覆盖旧映射。"""
        reg = AgentNameRegistry()
        reg.register("alice", "agent-1")
        reg.register("alice", "agent-2")
        assert reg.resolve("alice") == "agent-2"
        assert reg.name_of("agent-1") is None
        assert reg.name_of("agent-2") == "alice"

    def test_overwrite_agent_id(self) -> None:
        """同 agent_id 被不同 name 注册时，旧 name 被清除。"""
        reg = AgentNameRegistry()
        reg.register("alice", "agent-1")
        reg.register("bob", "agent-1")
        assert reg.resolve("alice") is None
        assert reg.resolve("bob") == "agent-1"

    def test_unregister(self) -> None:
        reg = AgentNameRegistry()
        reg.register("alice", "agent-1")
        reg.unregister("alice")
        assert reg.resolve("alice") is None
        assert reg.name_of("agent-1") is None

    def test_unregister_by_agent_id(self) -> None:
        reg = AgentNameRegistry()
        reg.register("alice", "agent-1")
        reg.unregister_by_agent_id("agent-1")
        assert reg.resolve("alice") is None

    def test_list(self) -> None:
        reg = AgentNameRegistry()
        reg.register("a", "1")
        reg.register("b", "2")
        d = reg.list_()
        assert d == {"a": "1", "b": "2"}
