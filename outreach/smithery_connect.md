# Smithery.ai — MolTrust MCP Server verbinden

## Status
- `smithery.yaml` ist im Repo: https://github.com/MoltyCel/moltrust-mcp-server/blob/main/smithery.yaml
- Server ist noch NICHT auf Smithery gelistet

## Voraussetzung
- GitHub Account (MoltyCel) muss bei Smithery eingeloggt sein

## Schritte

### 1. Smithery aufrufen
Gehe zu: **https://smithery.ai/new**

### 2. GitHub verbinden
- Falls nicht eingeloggt: "Sign in with GitHub" klicken
- GitHub OAuth autorisieren

### 3. Repository auswählen
- Nach dem Login zeigt smithery.ai/new eine Auswahl der eigenen GitHub Repos
- Wähle: **MoltyCel/moltrust-mcp-server**
- Smithery erkennt automatisch die `smithery.yaml` im Repo

### 4. Metadata-Scan
Smithery startet den Server via `uvx moltrust-mcp-server` und scannt automatisch:
- Alle 8 Tools (register, verify, reputation, rate, credential, credits, deposit, erc8004)
- Tool-Beschreibungen
- Config-Schema (optionaler API Key)

### 5. Review & Publish
- Smithery zeigt eine Vorschau der erkannten Tools
- Prüfe ob alle 8 Tools korrekt erkannt wurden
- Klicke "Publish" oder "Submit"

### 6. Verifizieren
Nach dem Publish sollte der Server erreichbar sein unter:
**https://smithery.ai/server/@MoltyCel/moltrust-mcp-server**

## smithery.yaml (aktuell im Repo)

```yaml
startCommand:
  type: stdio
  configSchema:
    type: object
    properties:
      moltrustApiKey:
        type: string
        description: "API key for authenticated MolTrust endpoints (optional)"
  commandFunction: |-
    (config) => ({
      command: 'uvx',
      args: ['moltrust-mcp-server'],
      env: Object.assign({},
        config.moltrustApiKey ? { MOLTRUST_API_KEY: config.moltrustApiKey } : {}
      )
    })
  exampleConfig: {}
```

## Alternative: CLI

Falls du die CLI bevorzugst:

```bash
# Node.js 20+ benötigt
npm install -g @smithery/cli@latest

# Login
smithery auth login

# Publish (stdio)
smithery mcp publish --name @MoltyCel/moltrust-mcp-server --transport stdio
```

## Nach dem Listing

- [ ] Link zu DIRECTORIES.md im Repo hinzufügen
- [ ] Badge im README ergänzen (falls Smithery Badges anbietet)
- [ ] PR-Monitor um Smithery-Eintrag erweitern (falls nötig)
- [ ] Vanity-URL `moltrust.ch/smithery` → Smithery-Listing (nginx redirect)
