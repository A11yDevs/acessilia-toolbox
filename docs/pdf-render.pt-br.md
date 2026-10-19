# Renderização de páginas (`pdf.render`)

> Esta é a tradução para português brasileiro de
> [pdf-render.md](pdf-render.md). A versão em inglês é a fonte
> autoritativa em caso de divergência.

`pdf.render` transforma uma página de um documento em uma imagem PNG. O
provider é o `pymupdf-pdf` (PyMuPDF, no próprio processo). Duas versões do
contrato são publicadas lado a lado.

| | Versão 1 | Versão 2 (mais recente) |
|---|---|---|
| Entradas | PDF | PDF, PNG, JPEG |
| Parâmetros | `page_number`, `dpi` | `page_number`, `dpi`, `max_side`, `clip` |
| Resultado | `artifact/image@1`, PNG inline em base64 | `artifact/image@2`, PNG guardado como artefato (`by_reference`) |
| Seleção | só com `version=1` explícito | padrão quando nenhuma versão é informada |

A versão 1 continua executável enquanto os clientes migram; as ferramentas MCP
e as ações PDDL expõem só a versão 2.

## Versão 2

### Parâmetros

| Nome | Tipo | Padrão | Significado |
|---|---|---|---|
| `page_number` | inteiro ≥ 1 | `1` | Página, a partir de 1. Uma entrada PNG ou JPEG tem exatamente uma página. |
| `dpi` | inteiro 1–600 | `150` | Resolução: a escala é `dpi / 72` pixels por unidade de página. |
| `max_side` | inteiro 16–10000 | nenhum | Limite, em pixels, do maior lado da saída. Junto com `dpi`, vale a menor escala. |
| `clip` | objeto | página inteira | Região a renderizar: `left`, `top`, `right`, `bottom` em unidades de página, mais `origin` (`TOPLEFT`, o padrão, ou `BOTTOMLEFT`). |

**Unidades de página** são pontos de PDF. Em entradas raster, são os pontos que
o PyMuPDF deriva da resolução da imagem: pixels a 72 dpi quando a imagem não
declara nenhuma. A resposta informa o tamanho da página nas mesmas unidades.

**Semântica do recorte:**
- Uma caixa `BOTTOMLEFT` é convertida para origem no topo com a altura da
  página.
- Cantos invertidos são normalizados.
- A caixa é intersectada com a página.
- Uma caixa que não toca a página, ou que deixa menos de uma unidade em alguma
  direção, é recusada.
- Campos desconhecidos, coordenadas não numéricas ou não finitas e outras
  origens são recusados.

**Limite:** uma renderização acima de 20.000.000 de pixels (largura × altura)
é recusada; reduza o `dpi` ou defina `max_side`.

### Resultado (`artifact/image@2`)

O PNG fica no armazenamento de artefatos e é baixado com
`GET /v1/artifacts/{artifact_id}`. A lista `artifacts` da resposta traz a
referência dele ao lado do documento JSON. O documento informa o que foi de
fato aplicado:

| Campo | Significado |
|---|---|
| `artifact_id`, `media_type`, `size_bytes` | O PNG guardado (`image/png`) |
| `page_number`, `page_count` | Página renderizada e total de páginas da fonte |
| `page_width`, `page_height` | Tamanho da página em unidades de página |
| `clip` | Região renderizada, origem no topo, já intersectada com a página (a página inteira sem recorte) |
| `scale`, `dpi` | Pixels por unidade de página, e `scale × 72` |
| `width_px`, `height_px` | Tamanho do PNG em pixels |
| `source_media_type`, `source_filename` | A entrada |

A renderização é determinística, idempotente e cacheável: a chave de cache
cobre a versão da capacidade, a impressão digital da entrada e os parâmetros.

## Versão 1

Mantida sem mudanças por compatibilidade: só entrada PDF, `page_number` e
`dpi`, e o PNG inline em `image_bytes_base64` (`artifact/image@1`). Peça-a com
`version=1`.
