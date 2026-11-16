# Contratos de respostas REST de datasets

Você também pode ler esta documentação em **inglês**: [English](dataset-contracts.md).

O `contract/toolbox-contract.json` publicado contém `dataset_routes`:
um vínculo explícito de método/caminho HTTP e versão exata da capacidade com
o schema da **resposta REST bruta**. O OpenAPI publica o mesmo objeto na
extensão `x-toolbox-dataset-binding` de cada operação de datasets. Resolver
`output_schema` pelo `schema_refs` do contrato fornece o arquivo publicado
em `schemas/`. Consumidores não precisam de imports Python da Toolbox nem
dos manifestos internos das capacidades.

```json
{
  "method": "GET",
  "path": "/v1/datasets/{dataset_id}/splits/{split}/items",
  "capability": "dataset.list_items",
  "output_schema": "artifact/dataset-item-summary-list@1",
  "capability_version": 1
}
```

Os modelos genéricos de objeto/array do OpenAPI permanecem para preservar
o comportamento de serialização e validação. A extensão e o mapa de rotas
exportado fornecem o vínculo normativo de schema, em vez de introduzir uma
serialização tipada que poderia acrescentar defaults, remover campos ou
alterar nulos. As rotas executam a versão do vínculo, atualmente 1, incluindo
a consulta de descrição que resolve o provider. Uma versão futura da capacidade
não substitui implicitamente o contrato da rota dedicada.

## Respostas publicadas

Os caminhos abaixo são relativos a `/v1/datasets`; `(base)` indica esse caminho
sem barra final. Os nomes de schemas omitem `artifact/`. Todas as coleções JSON são arrays na raiz, inclusive coleções
vazias `[]`. A resposta REST não tem envelope `document` nem de paginação.

| Método/caminho | Capacidade na versão 1 | Schema da resposta REST |
|---|---|---|
| `GET (base)` | `dataset.list` | `dataset-list@1`: array de `DatasetInfo` |
| `GET /{dataset_id}` | `dataset.describe` | `dataset-info@1`: um `DatasetInfo` |
| `GET /{dataset_id}/splits` | `dataset.list_splits` | `dataset-splits@1`: array de `SplitInfo` |
| `GET /{dataset_id}/splits/{split}/items` | `dataset.list_items` | `dataset-item-summary-list@1`: array de `ItemSummary` |
| `GET /{dataset_id}/splits/{split}/items/{item_id}` | `dataset.get_item` | `dataset-item@1`: um `DatasetItem` completo |
| `GET /{dataset_id}/splits/{split}/items/{item_id}/artifacts/{path}` | `dataset.get_artifact` | `binary@1`: bytes brutos, não um modelo JSON |
| `GET /{dataset_id}/splits/{split}/sample` | `dataset.sample` | `dataset-sample@1`: array de `DatasetItem` completos |
| `POST /{dataset_id}/sync` | `dataset.sync` | `dataset-sync-report@1`: array de eventos de sincronização |

`revision` mantém sua semântica opcional de revisão Git/HuggingFace.
A listagem de itens usa `limit` (1–1000, default 100) e `offset` (ao menos 0,
default 0). A amostragem usa `n` (1–100, default 5) e pode devolver menos
itens do que o solicitado. Sync permite restringir um `split`. As restrições
de consulta continuam publicadas no OpenAPI. Falhas externas ou datasets/itens
ausentes continuam sendo erros, e não listas vazias bem-sucedidas.

### Resumos e itens completos

`ItemSummary` descreve uma listagem leve: `id`, dataset/split/revision,
`artifact_count`, `has_annotations` e `metadata` aberto. Itens completos
contêm `artifacts` e `annotations` em seu lugar. Cada referência descreve
um `path` no dataset, tipo de mídia e metadados opcionais de conteúdo; não é
o contrato separado `artifact/reference@1`, endereçado por conteúdo.

Os manifestos execute existentes de `dataset.list_items@1` e
`dataset.sample@1` nomeiam `artifact/dataset-item-list@1`. Essa família
compartilhada é publicada como um array que aceita resumos ou itens completos.
Seu ramo permissivo de itens completos mantém a política de campos obrigatórios
de `dataset-item@1`; não é um discriminador nem substitui o schema preciso da
rota. Clientes que geram tipos REST devem selecionar o vínculo de rota acima,
em vez de deduzir o tipo de elemento da referência execute compartilhada.

Nenhum id de capacidade, versão ou referência de saída de manifesto muda.
A publicação da família compartilhada e dos schemas precisos de rotas não
exige alteração incompatível dos payloads existentes. Mudanças futuras que
não preservem esses contratos exigem uma nova versão, em vez de redefinir
silenciosamente as rotas da versão 1.

### Eventos de sincronização

Sync devolve array, não um objeto agregado ou contagem. Exemplos:

```json
[
  {"status": "mirrored", "item": "one", "split": "input", "artifact": "input/one.pdf"},
  {"status": "skipped", "item": "two", "split": "input", "reason": "item unavailable"},
  {"status": "skipped", "item": "three", "split": "input", "artifact": "input/three.pdf", "reason": "artifact unavailable"}
]
```

Quando o espelhamento está desativado, a resposta atual é
`[{"status":"skipped","reason":"mirroring is disabled"}]`. Uma sincronização
bem-sucedida sem itens retorna `[]`. Eventos espelhados exigem item, split e
artefato; eventos ignorados exigem motivo, com contexto de item/artefato quando
disponível. Isso publica o comportamento existente do adaptador; não altera
espelhamento, armazenamento, retries nem tratamento de falhas individuais.

## Manutenção de schemas e compatibilidade

Os schemas de objetos em `schemas/` continuam sendo os contratos de origem.
O exportador de coleções incorpora suas definições para que cada arquivo
gerado seja autocontido e utilize apenas referências locais.
`dataset-item@1` incorpora a mesma definição de `DatasetArtifactRef` que
`dataset-artifact@1`; um teste rejeita divergências entre elas.

Os schemas de objetos existentes são ampliados apenas para aceitar nulos
que os providers já serializam: `DatasetInfo.item_count`, `license`, `citation`
e `DatasetArtifactRef.sha256`/`artifact_id`. Campos obrigatórios, restrições
sobre valores não nulos e aceitação de campos de extensão são preservados.
Os schemas antigos rejeitavam respostas reais com defaults; a correção não
acrescenta campos no produtor nem muda valores serializados. Referências a
caminhos de datasets continuam distintas das referências do armazenamento de artefatos.

Após editar um schema de objeto ou os vínculos de rotas, executar na raiz:

```bash
python scripts/export_dataset_schemas.py
python scripts/export_contract.py
UPDATE_OPENAPI=1 pytest tests/integration/test_rest_api.py -k committed_openapi
python scripts/export_dataset_schemas.py --check
python scripts/export_contract.py --check
pytest tests/unit/test_dataset_contracts.py tests/unit/test_schema_files.py
```

Não editar manualmente schemas gerados de coleções, contrato estático ou
OpenAPI. As verificações de desvio e conformidade REST/MCP serializada
exercitam os providers GitHub e HuggingFace com HTTP externo simulado. Cobrem
campos anuláveis, referências aninhadas de artefatos, paginação, amostragem de
itens completos, resultados vazios, todos os formatos de eventos de sync,
leitura binária e erros. A aplicação geral de validação de saída de providers
em runtime continua sendo o trabalho separado da [#117](https://github.com/A11yDevs/acessilia-toolbox/issues/117);
esta mudança fornece os contratos públicos que ela pode consumir.

Isso resolve a lacuna de publicação acompanhada na [#158](https://github.com/A11yDevs/acessilia-toolbox/issues/158)
para o trabalho de [geração de bindings do SDK](https://github.com/A11yDevs/acessilia/issues/213).
A etapa de captura/geração no SDK deve manter `dataset_routes` e atualizar
os schemas selecionados a partir do mesmo commit imutável da Toolbox.
Decisões de allowlist, providers e retries continuam pertencendo ao SDK.
As mudanças de providers/referências do [PR #155](https://github.com/A11yDevs/acessilia-toolbox/pull/155)
devem preservar esses exemplos de comunicação ou atualizar seus contratos
explicitamente; após alterar schemas, regenerar coleções e executar a mesma suíte.
