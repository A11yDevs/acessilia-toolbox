# Exportação DOCX

> Esta é a tradução para português brasileiro de
> [docx-export.md](docx-export.md). A versão em inglês é a fonte
> autoritativa em caso de divergência.

``` text
document.export.docx : AST JSON do Pandoc -> documento Word (artefato armazenado)
```

O provider `pandoc-docx` chama a rota `/render/docx` do mesmo sidecar que
atende a [exportação PDF/UA](pdf-ua-export.md) (`pdf-ua-render`, construído a
partir de `docker/pdf-ua-render.Dockerfile`). Ele reaproveita o Pandoc fixado
nessa imagem e não precisa de TeX. Configure o endpoint com `PDF_UA_RENDER_URL`.

O Pandoc preserva a estrutura de que as tecnologias assistivas dependem: os
títulos viram estilos de título do Word, as tabelas mantêm a linha de
cabeçalho e as fórmulas viram Office Math, que os leitores de tela conseguem ler.

## Requisição

Envie o AST como `application/json`:

``` bash
curl -H "Authorization: Bearer $TOOLBOX_API_KEY" \
  -F file=@document.ast.json\;type=application/json \
  -F 'parameters={"lang":"pt-BR"}' \
  http://localhost:8002/v1/capabilities/document.export.docx:execute
```

| Parâmetro | Padrão | Significado |
|---|---|---|
| `lang` | `pt-BR` | Tag BCP 47 gravada como idioma do documento (`w:lang`) |

O renderizador não lê nada além do AST:

- Nós crus de OpenXML são recusados em qualquer ponto do AST: o sidecar
  responde `422`, que a API reporta como `400`. Sem isso, o OpenXML cru seria
  colado no documento sem verificação. Os demais formatos crus (TeX, HTML,
  Markdown, ...) são descartados pelo Pandoc sem aviso, e a requisição ainda
  é bem-sucedida.
- O Pandoc roda com `--sandbox`, então uma imagem ou inclusão que aponte para um
  arquivo local ou uma URL fica de fora em vez de ser lida. A imagem descartada
  é substituída pelo seu texto alternativo, o que mantém o documento legível;
  espera-se que as imagens já tenham sido reduzidas a alternativas textuais.

## Resposta

O DOCX nunca vem embutido na resposta. O executor o guarda no armazenamento de
artefatos e o resultado traz uma referência (schema `artifact/docx-export@1`):

``` json
{
  "document": {
    "artifact_id": "sha256:…",
    "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "size_bytes": 10521,
    "toolchain": {"version": "1.1.0", "pandoc": "3.9.0.2"}
  },
  "artifacts": [
    {"media_type": "application/json"},
    {"media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
  ]
}
```

Baixe-o com `GET /v1/artifacts/{artifact_id}`.

## Cache

A capacidade usa cache. A chave cobre o AST, `lang`, a versão do sidecar e a
versão do Pandoc. O TeX Live e o template de PDF/UA não influenciam o DOCX, então
atualizá-los mantém os DOCX em cache. Como na PDF/UA, um acerto de cache só é
servido enquanto o arquivo armazenado existir; por isso a capacidade exige um
armazenamento de artefatos.

O sidecar tem um único número de versão para as duas rotas, então qualquer
release do sidecar, mesmo uma que só corrija o caminho do PDF, também invalida
os DOCX em cache.

## Build

O build do sidecar também converte um AST de teste para DOCX, então um Pandoc
que não consiga gerar DOCX quebra o build. Veja a
[exportação PDF/UA](pdf-ua-export.md#build).

## Disponibilidade

O `/health` do sidecar verifica o Pandoc, a engine TeX e o template de PDF/UA,
embora `/render/docx` só precise do Pandoc. Uma instalação de TeX quebrada,
portanto, marca o sidecar inteiro como indisponível e derruba
`document.export.docx` junto. É o custo aceito de compartilhar uma imagem;
separe a rota DOCX em um sidecar próprio se a disponibilidade dela não puder
depender do TeX Live.
