# Provedor de Orientação de Página RapidOCR

> Esta é a tradução para português brasileiro de
> [rapidocr-orient.md](rapidocr-orient.md). A versão em inglês é a fonte
> autoritativa em caso de divergência.

A capability `image.page.orient` detecta a orientação natural de leitura de uma
página de imagem (0°, 90°, 180°, 270°) e, opcionalmente, retorna a imagem
endireitada.

## Arquitetura

Seguindo a [constituição do projeto](constitution.md), a Toolbox permanece livre
de runtimes de imagem e de aprendizado de máquina. A detecção de orientação de
página é executada em um container sidecar dedicado com RapidOCR e ONNX Runtime
(`rapidocr-orient`), seguindo o mesmo padrão de `docling-serve` e `mineru-api`.

- **Dockerfile do sidecar**: `docker/rapidocr-orient.Dockerfile`
- **Serviço do sidecar**: `docker/rapidocr-orient/server.py`
- **Definição no Compose**: `docker-compose.yml` (`rapidocr-orient:5006`)
- **Manifesto da capability**: `capabilities/image.page.orient.yaml`
- **Adaptador de provedor**: `src/acessilia_toolbox/providers/rapidocr_orient.py`

## Habilitação

O provedor é registrado em `providers-config.yaml` e permanece inativo até que o
sidecar esteja acessível. Defina `RAPIDOCR_ORIENT_URL` para sobrescrever o
endpoint; o valor padrão resolve para o nome do serviço no Compose.

```bash
docker compose up -d rapidocr-orient
curl -fsS http://localhost:5006/health
```

`GET /health` retorna `503` até que o motor de OCR esteja carregado, de modo que
o Compose nunca reporte um sidecar sem modelo como saudável.

## Execução direta da capability

```bash
curl -X POST "$TOOLBOX_URL/v1/capabilities/image.page.orient:execute" \
  -H "Authorization: ******" \
  -F 'provider=rapidocr-orient' \
  -F 'file=@page.jpg;type=image/jpeg'
```

O documento está em conformidade com `artifact/page-orientation@1`:

```json
{
  "angle": 90,
  "confidence": 0.91,
  "stage": "stage2_evaluated",
  "auto_rotated": true,
  "original_width": 1200,
  "original_height": 800,
  "oriented_width": 800,
  "oriented_height": 1200
}
```

`angle` é a rotação no sentido horário necessária para deixar a página
endireitada. Quando a requisição pede a imagem corrigida (`return_image`), o
payload também carrega `image_base64` e `media_type`.

## Estratégia de detecção

- **Estágio 1** lê a página uma vez. O classificador de ângulo do OCR resolve o
  caso de 180°, que mantém o texto na horizontal e, portanto, é invisível para
  heurísticas geométricas. Páginas já endireitadas são reportadas como
  `stage1_upright`.
- **Estágio 2** compara quão bem a página é lida a 0° versus 90° e 270°. Cada
  passagem pontua as linhas horizontais de texto pela confiança de
  reconhecimento, de modo que uma página virada de lado — cujos recortes ficam
  verticais ou ilegíveis — perde.

## Pré-processamento durante a extração

Passar `parameters={"auto_rotate": true}` faz o executor consultar
`image.page.orient` para entradas de imagem e entregar a imagem endireitada ao
motor de layout seguinte (Docling, MinerU, TeleOCR). O passo é opt-in e
desativado por padrão.

A decisão é registrada em `provenance.orientation`:

```json
{
  "angle": 90,
  "confidence": 0.91,
  "stage": "stage2_evaluated",
  "auto_rotated": true,
  "original_dimensions": [1200, 800],
  "oriented_dimensions": [800, 1200]
}
```

Se o sidecar estiver inacessível, a extração prossegue com a imagem original e o
motivo é registrado (`provider_failed`). Os bytes rotacionados passam a ser a
impressão digital da entrada, de modo que um resultado em cache sempre
corresponde ao payload efetivamente processado.