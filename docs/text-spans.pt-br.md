# Spans de texto (`document.text.spans`)

> Esta é a tradução para português brasileiro de
> [text-spans.md](text-spans.md). A versão em inglês é a fonte autoritativa em
> caso de divergência.

`document.text.spans` lê a camada de texto de um PDF como linhas de spans. Cada span traz sua fonte, tamanho, flags de estilo e geometria. O provider é o `pymupdf-pdf` (PyMuPDF, em processo). A capability é determinística e cacheável, e não roda nenhum modelo.

Ela existe para o que os modelos de layout não informam. Num PDF tipografado, a math inline só aparece nas fontes e nas linhas de base: fontes matemáticas do TeX (`CMMI`, `CMSY`, `CMEX`, `MSBM`), e spans menores acima ou abaixo da linha de base para os índices e expoentes. O core do Acessilia a usa para achar math inline nos parágrafos (A11yDevs/acessilia#265, ADR 0011 de lá).

## Parâmetros

| Nome | Tipo | Padrão | Significado |
|---|---|---|---|
| `pages` | lista de inteiros a partir de 1 | todas as páginas | Páginas a ler. Repetições são descartadas e as páginas voltam em ordem. Página fora do intervalo é recusada. |

Uma chamada lê no máximo 200 páginas. Um documento maior precisa indicar as páginas, em lotes. Uma página de texto denso custa cerca de 0,1 s e 50 KiB de JSON, então uma chamada cheia leva cerca de 20 s e devolve cerca de 10 MiB, que também vão para o cache e para o store.

## Resultado (`artifact/text-spans@1`)

O `s₀` de `s₀ ∈ ℝ` na página 1 do caso de referência de math do Acessilia (os outros spans foram omitidos):

```json
{
  "page_count": 5,
  "coord_origin": "TOPLEFT",
  "pages": [
    {
      "page_number": 1, "width": 612.0, "height": 792.0, "rotation": 0,
      "lines": [
        {
          "bbox": [64.92, 214.0, 532.67, 230.75],
          "spans": [
            {"text": "s", "font": "CMMI12", "size": 11.96, "flags": 4,
             "bbox": [498.96, 216.99, 504.47, 228.95], "origin": [498.96, 225.96]},
            {"text": "0", "font": "CMR8", "size": 7.97, "flags": 4,
             "bbox": [504.48, 221.78, 508.71, 229.75], "origin": [504.48, 227.76]}
          ]
        }
      ]
    }
  ]
}
```

- **Coordenadas.** São pontos PDF no espaço da página sem rotação, com origem no canto superior esquerdo e `y` crescendo para baixo. Uma página rotacionada informa `rotation` e mantém `width` e `height` sem rotação.
- **`origin`.** O início da linha de base do span. Um índice tem `y` maior que o texto em volta, e um expoente, menor.
- **`flags`.** Os bits de estilo do PyMuPDF: 1 sobrescrito, 2 itálico, 4 serifa, 8 monoespaçada, 16 negrito.
- **`font`.** O nome da fonte sem o prefixo de subset (`ABCDEF+CMMI12` vira `CMMI12`).
- **`text`.** O mapeamento de caracteres do PDF, que nem sempre é o texto visível. Uma fonte TeX na codificação OT1 desenha os acentos como glifos próprios, então `posição` volta como `posi¸c˜ao` e `é` como `´e`. Não compare `text` com o texto de um elemento de layout em palavras acentuadas; as fontes matemáticas (`CMMI`, `CMSY`, `MSBM`) não são afetadas.
- **Espaços.** Spans só de espaço são descartados, e os demais são aparados. O TeX escreve o espaço entre palavras como spans próprios, e o PyMuPDF insere espaços onde vê um vão. Um desses espaços no início de um índice informaria a linha de base do corpo do texto. Os vãos continuam visíveis nas caixas.
- **Blocos de imagem** são ignorados.
