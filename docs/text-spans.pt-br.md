# Spans de texto (`document.text.spans`)

> A versão em inglês está em [text-spans.md](text-spans.md).

`document.text.spans` lê a camada de texto de um PDF como linhas de spans. Cada span traz sua fonte, tamanho, flags de estilo e geometria. O provider é o `pymupdf-pdf` (PyMuPDF, em processo). A capability é determinística e cacheável, e não roda nenhum modelo.

Ela existe para o que os modelos de layout não informam. Num PDF tipografado, a math inline só aparece nas fontes e nas linhas de base: fontes matemáticas do TeX (`CMMI`, `CMSY`, `CMEX`, `MSBM`), e spans menores acima ou abaixo da linha de base para os índices e expoentes. O core do Acessilia a usa para achar math inline nos parágrafos (A11yDevs/acessilia#265, ADR 0011 de lá).

## Parâmetros

| Nome | Tipo | Padrão | Significado |
|---|---|---|---|
| `pages` | lista de inteiros a partir de 1 | todas as páginas | Páginas a ler. Repetições são descartadas e as páginas voltam em ordem. Página fora do intervalo é recusada. |

Uma chamada lê no máximo 1000 páginas. Um documento maior precisa indicar as páginas. Como uma página de texto denso dá cerca de 45 KiB de JSON, pedir em lotes também mantém cada resposta pequena.

## Resultado (`artifact/text-spans@1`)

O formato está em [text-spans.md](text-spans.md#result-artifacttext-spans1).

- **Coordenadas.** São pontos PDF no espaço da página sem rotação, com origem no canto superior esquerdo e `y` crescendo para baixo. Uma página rotacionada informa `rotation` e mantém `width` e `height` sem rotação.
- **`origin`.** O início da linha de base do span. Um índice tem `y` maior que o texto em volta, e um expoente, menor.
- **`flags`.** Os bits de estilo do PyMuPDF: 1 sobrescrito, 2 itálico, 4 serifa, 8 monoespaçada, 16 negrito.
- **`font`.** O nome da fonte sem o prefixo de subset (`ABCDEF+CMMI12` vira `CMMI12`).
- **Espaços.** Spans só de espaço são descartados, e os demais são aparados. O TeX escreve o espaço entre palavras como spans próprios, e o PyMuPDF insere espaços onde vê um vão. Um desses espaços no início de um índice informaria a linha de base do corpo do texto. Os vãos continuam visíveis nas caixas.
- **Blocos de imagem** são ignorados.
