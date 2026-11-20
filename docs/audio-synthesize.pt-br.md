# Capacidade de Síntese de Áudio (`audio.synthesize`)

A capacidade `audio.synthesize` converte conteúdo em texto puro ou markdown em
artefatos de áudio falado (`audio/mpeg` ou `audio/wav`), provendo representações
faladas acessíveis de documentos.

## Visão Geral

Seguindo os princípios arquiteturais do ecossistema Acessilia:
- **Princípio 4 da Constituição (Isolamento de Provedores):** Bibliotecas leves ou
  baseadas em nuvem rodam in-process, enquanto motores neurais e runtimes pesados de
  aprendizado de máquina rodam fora do processo em sidecars dedicados.
- **Princípio 5 (Trânsito por Referência de Artefatos):** O áudio binário sintetizado
  é armazenado diretamente no repositório de artefatos configurado (MinIO ou fallback
  no sistema de arquivos local). A resposta da capacidade retorna uma referência
  imutável (`audio-export@1`), nunca embutindo bytes de áudio nas cargas da API ou MCP.
- **ADR-0003 & Issue #73:** Elimina o desvio operacional de síntese de áudio in-process
  no core agentic, centralizando a execução na Toolbox.

---

## Provedores

| Provedor | Transporte | Formato de Saída | Offline / Local | Motor |
|---|---|---|---|---|
| `edge-tts` | `in_process` | `audio/mpeg` (MP3) | Não (API Nuvem) | Microsoft Edge Speech Service |
| `piper-tts` | `http` (sidecar) | `audio/wav` (WAV) | Sim (100% Offline) | Piper ONNX Runtime |

### 1. `edge-tts` (In-Process)

O `edge-tts` comunica-se com o serviço de voz da Microsoft via WebSockets, sem demandar
pesos de modelos neurais locais (pacote Python de ~70KB).

Instale com o extra opcional `audio`:

```bash
pip install "acessilia-toolbox[audio]"
```

**Recursos:**
- Particionamento em nível de parágrafo (padrão de 1500 caracteres) com divisão em
  fronteiras de sentenças para parágrafos excessivamente longos.
- Síntese assíncrona de blocos com concorrência limitada (`concurrency=5`).
- Concatenação das partes em um fluxo contínuo MP3.

**Parâmetros:**
| Parâmetro | Tipo | Padrão | Descrição |
|---|---|---|---|
| `voice` | `str` | `pt-BR-FranciscaNeural` | Identificador da voz (ex.: `pt-BR-AntonioNeural`, `en-US-AriaNeural`) |
| `language` | `str` | `pt-BR` | Código do idioma (ex.: `pt-BR`, `en-US`) |
| `rate` | `str \| float` | `null` | Ajuste de velocidade (ex.: `"+20%"`, `1.2`, `"-10%"`) |
| `volume` | `str \| float` | `null` | Ajuste de volume (ex.: `"+10%"`, `0.8`) |
| `pitch` | `str \| int` | `null` | Ajuste de tom (ex.: `"+50Hz"`, `"-20Hz"`) |
| `chunk_size` | `int` | `1500` | Tamanho máximo em caracteres por bloco de texto |
| `concurrency` | `int` | `5` | Máximo de requisições concorrentes de síntese de blocos |

---

### 2. `piper-tts` (Sidecar Docker HTTP)

O `piper-tts` oferece síntese neural de texto para fala local, de baixa latência e offline
usando ONNX Runtime (< 0.1 Real-Time Factor em CPUs modernas).

Pelo Princípio 4, roda como um serviço sidecar HTTP isolado:
- **Dockerfile:** `docker/piper-tts.Dockerfile`
- **Porta Padrão:** `5012`
- **Modelo Padrão:** `pt_BR-faber-medium` (baixado no build e armazenado em `/models`)
- **Imagem Publicada:** `ghcr.io/a11ydevs/acessilia-toolbox-piper-tts`

**Configuração (`.env`):**

```bash
PIPER_TTS_URL=http://piper-tts:5012
PIPER_TTS_PORT=5012
# PIPER_TTS_IMAGE=ghcr.io/a11ydevs/acessilia-toolbox-piper-tts:develop
```

**Parâmetros:**
| Parâmetro | Tipo | Padrão | Descrição |
|---|---|---|---|
| `voice` | `str` | `pt_BR-faber-medium` | Nome do modelo de voz em `/models` (ex.: `pt_BR-edresson-low`) |
| `language` | `str` | `pt-BR` | Tag de idioma |
| `speaker_id` | `int` | `null` | ID do locutor em modelos multi-speaker |
| `length_scale` | `float` | `null` | Escala de velocidade/duração (ex.: `1.1` para mais lento, `0.9` mais rápido) |
| `noise_scale` | `float` | `null` | Escala de ruído de fonemas (padrão `0.667`) |
| `noise_w` | `float` | `null` | Escala de largura de ruído de fonemas (padrão `0.8`) |

---

## Esquema de Saída (`schemas/audio-export@1.json`)

Os metadados de saída cumprem com `schemas/audio-export@1.json`:

```json
{
  "artifact_id": "sha256:d8a2...3f",
  "media_type": "audio/mpeg",
  "size_bytes": 105432,
  "duration_seconds": 12.45,
  "voice": "pt-BR-FranciscaNeural",
  "language": "pt-BR",
  "chunk_count": 3,
  "toolchain": {
    "provider": "edge-tts",
    "edge_tts": "6.1.12"
  }
}
```

---

## Exemplo de Execução via API

Execução via REST:

```bash
curl -X POST http://localhost:8000/v1/capabilities/audio.synthesize:execute \
  -F "file=@capitulo1.txt;type=text/plain" \
  -F 'parameters={"voice":"pt_BR-faber-medium"}' \
  -F "provider_id=piper-tts"
```

A resposta contém os metadados de execução e o ID do artefato binário. O arquivo
de áudio pode ser baixado em `/v1/artifacts/{artifact_id}`.
