# Explanation

Explanation documentation provides **conceptual understanding** of how popup-ai works and why it's designed the way it is.

## Available Explanations

### [Architecture Overview](architecture.md)

High-level view of popup-ai's design.

**Topics covered:**

- System components
- Data flow through the pipeline
- How the UI integrates with actors
- Design decisions and trade-offs

### [Ray Actors](ray-actors.md)

Deep dive into the Ray actor architecture.

**Topics covered:**

- Why Ray for this project
- Actor isolation and fault tolerance
- Queue-based communication
- Supervision and restart policies

## Design Philosophy

popup-ai is built on several key principles:

### Modular Monolith

A single process with isolated components. Not microservices, but not a tangled monolith either. Each actor is independent and testable.

### Fault Isolation

If the LLM annotator fails, transcription continues. If transcription crashes, it restarts without affecting the overlay. Ray actors provide this isolation naturally.

### UI Independence

The admin UI is always available, even when the pipeline is stopped. You can start, stop, and monitor actors without restarting the application.

### Configuration Over Code

Runtime behavior is controlled via configuration, not code changes. Want to disable the overlay? Set an environment variable. Want a different model? Change a setting.
