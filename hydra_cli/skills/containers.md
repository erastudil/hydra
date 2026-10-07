id: containers
keys: docker, container, compose, podman
---

inspection : docker ps and docker logs name the running set. podman ps applies when docker is absent.

discovery : read the compose file in the worktree before asserting a service name.

claim : a container change stays unverified until the stated ps or health command exits 0.
