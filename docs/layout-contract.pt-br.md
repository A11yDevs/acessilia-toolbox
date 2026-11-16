# Contrato do resultado de análise de layout

Fonte em inglês: [Layout-analysis result contract](layout-contract.md).

`document.layout.analyze@1` publica `artifact/layout-analysis@1` em
[`schemas/layout-analysis@1.json`](../schemas/layout-analysis@1.json). Esse
JSON Schema descreve o **`document`** da resposta de execução, não o envelope
REST, a proveniência nem as referências de artefatos. O MCP serializa esse
documento como JSON em seu conteúdo de texto.

## Variantes existentes da versão 1

A versão 1 tem duas representações existentes no protocolo. Publicar sua
união preserva ambas; isso não torna seus campos intercambiáveis:

| Representação | Provider atual | Campos obrigatórios do documento |
|---|---|---|
| Layout compacto (`LayoutPages`) | `docling-layout` | `pages`, `page_count`, `region_count` |
| Manifesto de processamento (`MineruLayoutResult`) | `mineru-layout` | Campos existentes de `processing-manifest:1.1.0`, mais metadados de layout em `extractor.configuration` |

Ambas serializam `pages` como **array**. O mapa ou a lista de páginas de origem
do Docling é convertido nesse array; o mapa de origem não constitui uma
terceira variante de resultado. O leitor atual do SDK, que tolera mapas,
não define um contrato público de mapa. Os consumidores precisam identificar
a representação antes de ler regiões. Em particular, o helper existente
`analyze_pages` do SDK, sozinho, não extrai os metadados de classificação do
MinerU do manifesto de processamento.

Uma representação comum futura que substitua qualquer payload exige uma nova
versão de capacidade/schema e migração explícita dos clientes. Esta publicação
não altera providers, execução, transporte, retries nem seleção do SDK.

## Layout compacto

Exemplo do resultado atual do Docling:

```json
{
  "pages": [{
    "page_number": 2,
    "width": 595,
    "height": 842,
    "regions": [{
      "type": "table",
      "label": "table",
      "bbox": [50, 342, 500, 642],
      "confidence": 0.95,
      "text": "Table",
      "page_number": 2
    }]
  }],
  "page_count": 1,
  "region_count": 1
}
```

Os números de página são os números de origem baseados em um, não posições no
array. Largura e altura estão em pontos de página; `0` representa dimensão
desconhecida. As quatro coordenadas de uma região são
`[left, top, right, bottom]` em pontos de página, com origem superior esquerda
e y crescendo para baixo. Caixas com origem inferior esquerda são invertidas
usando a altura da página; uma origem não resolvida ou altura ausente para essa
conversão faz a região ser omitida, em vez de declarar outra convenção.

`type` é um de `text_clean`, `text_scanned`, `embedded_image`, `table`,
`formula`, `code_block`, `list_block`, `callout_box`, `unknown` ou `ignore`.
O adaptador omite regiões classificadas como `ignore`. `label` mantém o rótulo
de origem. `confidence` é uma probabilidade ou nulo; a ausência de confiança
na origem atualmente vira `1.0`. `text` pode ser nulo. Todos os campos do
exemplo são obrigatórios; documentos compactos, páginas e regiões aceitam
campos de extensão.

Somente páginas com regiões aceitas são emitidas. Sem nenhuma, o resultado é
`{"pages":[],"page_count":0,"region_count":0}`, mesmo que exista geometria
de páginas na origem. As contagens se referem às páginas e regiões emitidas,
não necessariamente a todas as páginas do arquivo de origem.

## Manifesto de processamento e classificações do MinerU

O segundo ramo incorpora o contrato existente
[`structured-document@1.json`](../schemas/structured-document@1.json).
Seus campos canônicos de documento/geometria continuam pertencendo ao
docstruct e sendo verificados pelos testes semânticos existentes. Nenhum
novo modelo canônico de página, elemento ou caixa é introduzido aqui.

O exemplo abaixo é um **fragmento** de `extractor.configuration`, não um
documento completo de layout:

```json
{
  "backend": "mineru-layout",
  "region_count": 1,
  "layout_regions": [{
    "type": "text",
    "source_label": "text",
    "raw_label": "text",
    "page": 0,
    "bbox": {"left": 50, "top": 100, "right": 500, "bottom": 200},
    "text_preview": "Text"
  }]
}
```

Os três campos de configuração e todos os campos de região do exemplo são
obrigatórios. O `type` de classificação é `text`, `title`, `list_block`,
`table`, `embedded_image`, `formula`, `ignore` ou `unknown`. `source_label`
é o rótulo de origem normalizado pelo adaptador; `raw_label` mantém o rótulo
externo. `text_preview` contém o preview atual de no máximo 120 caracteres,
ou nulo. Não existe campo de confiança; os consumidores não devem inventá-lo.

O `page` dos metadados é baseado em zero; páginas canônicas e proveniência de
elementos usam `page_number` baseado em um. A caixa dos metadados não contém
origem. Quando existe geometria da página de origem, as coordenadas
normalizadas do MinerU são convertidas em pontos de página. Sem ela, podem
continuar normalizadas; os consumidores precisam consultar a proveniência
canônica do elemento e seu `coord_origin` (`TOPLEFT`, `BOTTOMLEFT` ou `UNKNOWN`)
antes de interpretar a geometria. Origem desconhecida não promete pontos de
página.

Um resultado bem-sucedido sem blocos mantém as páginas canônicas disponíveis,
com `elements` e `layout_regions` vazios e contagem zero de classificações.
O manifesto canônico mantém suas regras existentes de campos obrigatórios,
opcionais e nulos, além da política de extensões; a configuração de layout e
os registros de classificação aceitam extensões. Exceções dos providers
continuam como erros REST ou texto `Error:` no MCP, em vez de documentos
vazios fabricados como sucesso.

## Manutenção e validação

Regenere com `python scripts/export_layout_schema.py` e
`python scripts/export_contract.py`. Ambos aceitam `--check`. O schema de
layout usa Draft 2020-12 e somente referências locais. Seu gerador incorpora
diretamente as definições e restrições do schema canônico; apenas os metadados
de identificador, dialeto e descrição da raiz são substituídos. Os testes
rejeitam divergências no arquivo gerado e nas definições canônicas incorporadas.

O JSON Schema valida formatos e restrições escalares. Igualdade de contagens,
correspondência de páginas/regiões e ordenação de caixas não são verificações
entre campos do JSON Schema; testes representativos dos providers verificam
esses valores emitidos. Os testes de conformidade invocam providers reais e
o executor por REST e MCP, simulando somente o HTTP externo. Cobrem mapas e
listas de páginas do Docling, o fixture publicado de MiddleJson do MinerU 4,
resultados vazios, geometria e falhas. Esta publicação não adiciona validação
em execução de respostas arbitrárias dos providers; isso permanece na
[#117](https://github.com/A11yDevs/acessilia-toolbox/issues/117).

Isso resolve o pré-requisito de contrato da
[#159](https://github.com/A11yDevs/acessilia-toolbox/issues/159). O trabalho
posterior de [bindings do SDK](https://github.com/A11yDevs/acessilia/issues/213)
deve capturar este schema e o contrato no mesmo SHA imutável do Toolbox,
tratar as variantes explicitamente e reutilizar docstruct para o ramo canônico.
