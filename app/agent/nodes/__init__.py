"""
app/agent/nodes — LangGraph node implementations by business domain.

Each module contains the node functions for one domain concern.
All functions accept ``(self, state)`` where ``self`` is a
``LegalGraphAgent`` instance, preserving the original calling
convention used by LangGraph's ``agent.xxx_node`` registration.
"""
