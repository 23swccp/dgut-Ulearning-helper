import unittest
from types import SimpleNamespace

from agent_tools import build_registry


class FakeBackend:
    token = "present"
    courses = [object()]
    selected_course = object()

    def course_helper_status(self):
        return {"running": False, "connected": False, "controllerState": "IDLE", "page": {}, "video": {}}


class AgentToolTests(unittest.TestCase):
    def test_first_read_only_tools_are_self_describing(self):
        registry = build_registry(FakeBackend(), instance_id="instance_test")
        names = [item["name"] for item in registry.capabilities()]
        self.assertEqual(names, sorted(names))
        self.assertEqual(names, ["course.get_status", "system.capabilities", "system.health", "system.version"])
        self.assertTrue(all(item["readOnly"] for item in registry.capabilities()))
        self.assertEqual(registry.call("system.version", {})["instanceId"], "instance_test")
        self.assertEqual(registry.call("system.health", {})["service"], "ready")


if __name__ == "__main__":
    unittest.main()
