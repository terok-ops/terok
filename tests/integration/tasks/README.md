# tasks/

Integration tests for task metadata lifecycle: creating tasks, listing them,
renaming them, showing status, and archiving them on delete. These are real
filesystem workflows but intentionally avoid podman-dependent task execution.
