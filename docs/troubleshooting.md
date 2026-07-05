# Troubleshooting

## Claude Code OAuth-Refresh-Bug (Pro/Max) — verifiziert 20.06.26
GitHub Issues #44092, #48079, #61045, #61912.
Symptom: nach Token-Ablauf wird Refresh-Token nicht genutzt, alle Befehle inkl. /login → 401.
Recovery:
1. security delete-generic-password -s "Claude Code-credentials"   # macOS-Keychain
2. ~/.claude/ purgen
3. `claude` neu starten → erzwingt OAuth-Browser-Flow
Abzugrenzen: Telekom-CGNAT-DDoS-Filter (Socket-Reset OHNE 401, nicht Auth-Error).
