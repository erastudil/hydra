import re
with open('tests/test_native_tools.py', 'r') as f:
    text = f.read()

text = text.replace('assert len(schemas) == 9', 'assert len(schemas) == 11')
text = text.replace('"invoke_subagent",\n        "swarm_fanout",\n    }', '"invoke_subagent",\n        "swarm_fanout",\n        "compact_context",\n        "retrieve_context",\n    }')

with open('tests/test_native_tools.py', 'w') as f:
    f.write(text)
