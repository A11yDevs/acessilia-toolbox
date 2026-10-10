# Provedor de Orientação de Páginas RapidOCR

A capability `image.page.orient` detecta a orientação natural de leitura de uma página
de imagem (0°, 90°, 180°, 270°) e opcionalmente retorna a imagem reorientada (rotacionada no sentido horário).

## Arquitetura

Em conformidade com a [constituição do projeto](constitution.md), a Toolbox permanece 100% leve,
sem dependências pesadas de aprendizado de máquina em seu ambiente Python principal. A detecção de
orientação de páginas é executada por meio de um container sidecar dedicado executando RapidOCR com
ONNX Runtime (`rapidocr-orient`).

- **Dockerfile do Sidecar**: `docker/rapidocr-orient.Dockerfile`
- **Serviço do Sidecar**: `docker/rapidocr-orient/server.py`
- **Definição no Compose**: `docker-compose.yml` (`rapidocr-orient:5006`)
- **Esquema da Capability**: `capabilities/image.page.orient.yaml`
- **Adaptador de Provedor**: `src/acessilia_toolbox/providers/rapidocr_orient.py`

## Uso

### Execução Direta da Capability

```bash
curl -X POST "$TOOLBOX_URL/v1/capabilities/image.page.orient:execute" \
  -H "Authorization: Bearer $TOOLBOX_API_KEY" \
  -F 'provider=rapidocr-orient' \
  -F 'file=@pagina.jpg;type=image/jpeg'
```

Resposta JSON:
```json
{
  "detected_angle": 90,
  "confidence": 0.95,
  "oriented": true,
  "width": 1119,
  "height": 666
}
```

### Pré-processamento via Flag Auto-Rotate

Ao executar a extração estrutural de documentos com `auto_rotate=True`, o executor consulta automaticamente
o serviço `image.page.orient` (caso disponível e se a mídia for uma imagem suportada) e fornece a imagem
já orientada para os motores de layout downstream (Docling, MinerU, TeleOCR).
