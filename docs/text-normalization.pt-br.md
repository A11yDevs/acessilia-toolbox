# Normalização de texto delimitada (`document.text.normalize@1`)

Inglês: [English version](text-normalization.md).

O provider `docstruct-text` executa **uma passagem** do algoritmo `norm_fix`
da Docstruct fixada, no processo existente da Toolbox. Expande ligaturas,
remove hífens suaves, substitui NBSP, une hifenizações elegíveis no fim de linha
e colapsa espaços/tabs duplicados. Espaços nas extremidades são preservados,
salvo alteração pelo algoritmo. Texto cujo prefixo após remover espaços
iniciais seja `<table`, `$$`, `\[` ou `|` permanece intacto. Não há inserção de
marcadores ou deduplicação; `text.postprocess@1` continua separado e inalterado.

Esta é a parte do provider da [Acessilia #929](https://github.com/A11yDevs/acessilia/issues/929),
conforme a [ADR-0032 aceita](https://github.com/A11yDevs/acessilia/blob/develop/docs/adr/0032-toolbox-runtime-library-placement.pt-br.md).
O Core mantém descoberta de alvos, estado de obrigações, metadados e aceite.
O novo pacote `acessilia_toolbox.operations` é interno e publicado com a Toolbox;
não importa Core ou SDK. A guarda de dependências inclui `acessilia_core`,
`acessilia_sdk`, `backend` e `frontend`.

## Requisição e resposta

Enviar arquivo JSON UTF-8 para
`POST /v1/capabilities/document.text.normalize:execute`, com os campos multipart
`provider=docstruct-text`, `version=1` e a autenticação Bearer existente.
Não há parâmetros de execução. Schema de entrada:
[`artifact/text-normalization-request@1`](../schemas/text-normalization-request@1.json).

```json
{"targets": [{"id": "paragraph-1", "text": "ﬁnal  text"}, {"id": "empty", "text": null}]}
```

O `CapabilityResult.document` padrão contém `results`, `processed_count` e
`changed_count`. Cada resultado tem `id` correspondente, `text` normalizado,
`input_fingerprint`, `processed` e `changed`. Schema de saída:
[`artifact/text-normalization-result@1`](../schemas/text-normalization-result@1.json).

- Exatamente um resultado por alvo, na ordem recebida; ids distintos podem
  conter o mesmo texto. Ids duplicados são rejeitados antes da transformação.
- `null` e `""` são preservados com ambas as flags falsas. Qualquer texto não
  vazio, inclusive espaços ou conteúdo protegido, conta como processado;
  texto inalterado tem `changed=false`. As contagens somam essas flags;
  zero não decide se uma obrigação do Core foi satisfeita.
- `input_fingerprint` é `sha256:` seguido do digest hexadecimal do JSON
  canônico UTF-8 do **original** `{"id": id, "text": text}`: chaves ordenadas,
  separadores `(',', ':')`, `ensure_ascii=False`, sem newline. Nulo e vazio
  continuam distintos.
- Consumidores devem validar ids únicos/correspondentes, contagens, flags,
  versões de capacidade/provider e fingerprints antes de aplicar observações.
  O Core verifica se texto/contexto processados continuam aplicáveis; um id
  estável não autoriza sobrescrever revisão mais recente. Aplicar
  `norm_fixed=true` apenas a alvos alterados, preservando os demais metadados,
  como fazia o handler antigo.
- Campos extras, manifestos de planejamento, textos/ids não string, chaves
  JSON duplicadas, UTF-8 inválido, surrogates isolados e constantes não finitas
  são rejeitados. Validadores do modelo verificam ids únicos e contagens
  consistentes além das restrições estruturais do JSON Schema.

As coleções validadas de alvos/resultados são snapshots de tuplas imutáveis
internamente; seus elementos e campos de contagem também são congelados.
O JSON público continua usando arrays. Apenas alvos de entrada expõem o helper
interno de fingerprint; observações carregam o `input_fingerprint` original
separadamente do texto de saída.

Os schemas são gerados dos DTOs operacionais Pydantic estritos, sem copiar
o envelope de planejamento do Core:

```bash
python scripts/export_text_normalization_schemas.py
python scripts/export_text_normalization_schemas.py --check
python scripts/export_contract.py
python scripts/derive_providers_config.py --check
```

## Limites, repetição e ciclo de vida

- Até **256 alvos**, ids de **1–256 caracteres** e **1.048.576 bytes** na
  requisição serializada inteira. O adaptador verifica os bytes antes do
  decode JSON; quantidade de alvos/tamanho de ids são verificações do modelo
  após o parsing. O executor genérico pode já ter calculado o fingerprint ou
  materializado a entrada sob seus limites globais. Portanto, 1 MiB não é teto
  de memória de ponta a ponta nem limite da resposta inteira; os limites
  globais de upload/spooling e memória continuam válidos.
- O orçamento/hint de parsing/computação do adaptador é **5 segundos**, ou timeout menor do provider.
  O adaptador verifica o tempo após computação delimitada e recusa resultado
  atrasado com `provider_timeout`; não interrompe código síncrono da Docstruct.
  A admissão usa o limite existente do processo (quatro execuções por padrão,
  `TOOLBOX_MAX_CONCURRENT_EXECUTIONS`), sem novo pool ou serviço.
- Repetir a **mesma entrada** é determinístico e não altera documento/workflow.
  Após perda de resposta, repetir a requisição original imutável; não normalizar
  o texto devolvido novamente como retry. Uma passagem não promete ponto fixo:
  `a-\nb-\nc` vira `ab-\nc` na Docstruct 0.5.8.
- Ids de job/operação, digests de entrada e chaves de cache têm papéis distintos.
  Usar novo id de operação quando entrada/provider/versão mudar. A junção atual
  é local ao processo e pode ser expulsa/reiniciada; não é idempotência
  distribuída durável. A junção existente não valida a identidade completa da
  entrada ([Toolbox #173](https://github.com/A11yDevs/acessilia-toolbox/issues/173));
  consumidores não devem reutilizar ids para trabalho alterado ou pular a
  verificação de fingerprints. O executor observa cancelamento antes/depois do provider
  e antes da publicação; reconhecimento não significa preempção síncrona.
  Cancelamento observado nessas verificações impede o trabalho de publicação
  subsequente; não desfaz gravações já iniciadas ou concluídas entre elas.
  Publicações em storage e cache não são uma transação atômica. O orçamento
  do adaptador não limita upload, fingerprint/materialização genéricos,
  cache/storage ou construção da resposta.
- A proveniência registra fingerprint do payload original, versões exatas de
  capacidade/provider, runtime Toolbox e Docstruct/algoritmo; essas versões de
  componentes também entram na chave do cache existente. Não há substituição
  semântica oculta de provider.
- Resultados usam o caminho existente de JSON inline/artefato/cache. Não são
  criados artefatos binários ou armazenamentos privados. A implantação continua
  dona da retenção configurada; falha de storage segue o comportamento existente
  do executor.
- Falhas: `invalid_input` (400), excesso de bytes `upload_too_large` (413),
  mídia incorreta `unsupported_media_type` (415), identidade/versão não
  suportada pelo adaptador ou falha de algoritmo `provider_execution_failed`
  (502), orçamento expirado observado `provider_timeout` (504). Falha do
  adaptador não publica lote parcial.

## Ordem de migração e evidências

Mesclar guarda/contrato/provider antes de publicar imagem da Toolbox e trocar
o Core. A integração deve fixar essa versão, preservar comportamento/checkpoints,
rejeitar respostas obsoletas/inválidas, reduzir a permissão `norm-fix` e manter
falha remota explícita. Toolbox antiga resulta em falha de versão/capacidade,
sem fallback computacional local. Preservar capacidades antigas para os
consumidores antigos e rollback.

Os testes do provider cobrem despacho/executor/REST reais, casos do algoritmo,
limites de lote, rejeição de entradas, cache/proveniência e cancelamento antes
de storage/cache. Evidências diferenciais e de checkpoint do Core pertencem à
integração do consumidor; este PR de provider sozinho não migra o método.
Medidas de lote e bytes são evidência local, não afirmação de latência ou
qualidade em produção.
