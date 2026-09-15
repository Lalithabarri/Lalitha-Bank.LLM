"""discovery/ — Observe -> decide -> act loop, stopping reasons.

Owns: ``DiscoveryAgent`` and the normalized trace (ARCHITECTURE §3, §5).
Must never: call ``Surface.act()`` directly — every action goes through ActionGate.

Implemented at ARCHITECTURE §15 step 11.
"""
