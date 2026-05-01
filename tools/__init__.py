"""Tools — the verbs personas can invoke.

A tool wraps one capability (a service call, an agent invocation, or a UI
mutation) in a uniform async-function shape. Tools are the only path
between personas and agents/services. Personas never import agents or
service internals directly.

V1 has a bare protocol — async functions, free signatures. The formal
`Tool` base class with input_schema/output_schema lands when more than one
tool exists and the LLM-driven tool selection loop is wired up.
"""
