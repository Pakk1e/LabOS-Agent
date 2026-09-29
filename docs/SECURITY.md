# Security Model

## Default exposure
The LabOS web control plane is local-only by default. Binding to a non-loopback host requires LABOS_ALLOW_REMOTE=1. Remote exposure should only be enabled behind an authenticated, trusted network boundary.

## Input boundaries
JSON request bodies are limited to 1 MiB. JSON content types are validated. Project names use a restricted identifier format, GitHub repositories use an owner/name format, project URLs require HTTP(S), and selected text fields have bounded lengths.

## Filesystem boundaries
Lifecycle state paths reject path separators and dot-directory traversal. Project roots are explicit absolute paths because LabOS manages existing engineering repositories; existing-repository mode additionally requires a local .git directory.

## Lifecycle authorization
DEVELOPMENT is the only phase requiring human approval. The supervisor independently checks that approval, so starting the supervisor cannot bypass the lifecycle gate through the web UI.

## State integrity
Configuration and lifecycle state use atomic replacement. Lifecycle state retains a backup of the previous known-good state so a corrupted primary can be recovered.

## Threat model limitations
The current local control plane is not a complete internet-facing authentication system. Do not expose it directly to an untrusted network. Future remote operation should add authentication, authorization, CSRF protection where applicable, and transport security.
