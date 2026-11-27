# Identidade da requisição de operação

Inglês: [English version](operation-identity.md).

Um `operation_id` identifica uma execução lógica dentro de uma tarefa. Reutilizá-lo
retorna a resposta ou erro de execução original apenas quando a requisição
completa corresponde. Contra uma identidade selada e retida, requisição validada
conflitante recebe o erro existente `400 invalid_input`;
requisições malformadas ou não suportadas mantêm seus erros
normais de validação. Outra tarefa recebe `404 operation_not_found`; a autenticação
é verificada primeiro. `attempt_id` muda a cada tentativa HTTP e não participa da
identidade. Chamadas sem os headers de operação mantêm a execução independente.

Isso aplica a invariante de retry da
[ADR-0019 aceita](https://github.com/A11yDevs/acessilia/blob/develop/docs/adr/0019-operation-identifiers-and-cancellation.pt-br.md)
e corrige [#173](https://github.com/A11yDevs/acessilia-toolbox/issues/173).
Não acrescenta versão de capacidade, campo público, código de erro, armazenamento
ou estado de workflow. O registro local ao processo mantém apenas um digest
privado junto ao resultado existente; não expõe o digest nem seu material de origem.

## O que precisa corresponder

| Campo | Regra de correspondência |
|---|---|
| Rota | Upload de artefato e execução de capacidade são distintos, inclusive para `artifact.store`. |
| Capacidade | Id/versão resolvidos e dados de vinculação do manifesto. |
| Provider | Descritor resolvido, incluindo versão e configuração. |
| Entrada enviada | Bytes exatos, nome efetivo do arquivo e tipo de mídia. |
| Entrada referenciada | Id canônico `sha256:`; o digest equivalente sem prefixo corresponde. |
| Parâmetros | Objeto JSON interpretado, chaves ordenadas e codificação UTF-8 compacta; ordem de chaves e espaços não importam. |
| Idioma/cache | Idioma e `no_cache` efetivos, incluindo valores padrão. |

O id do provider e a versão da capacidade resolvidos são passados à execução,
selecionando o padrão antes da vinculação. Retry que resolve padrões ou
configuração diferentes é recusado. Tipos JSON dos parâmetros e ordem dos arrays
permanecem distintos; números não finitos e strings UTF-8 inválidas são recusados.
Arquivo JSON enviado continua sendo comparado por bytes: reformatá-lo altera a
requisição. Nome e tipo de mídia importam porque os providers podem observá-los.

Registries são carregados na criação da aplicação e permanecem estáveis durante
requisições. Não há hot reload concorrente suportado. Fixar o id do provider e a
versão da capacidade não transforma configuração mutável em snapshot; alterações exigem
nova aplicação/implantação. Testes que mudam configuração entre requisições
verificam detecção de divergência, não mutação suportada durante a execução.

Retries de artefatos comparam a referência imutável do chamador sem novo download.
Nome/tipo de mídia resolvidos do storage pertencem à execução e ao resultado
originais. Indisponibilidade ou alteração posterior de metadados não impedem
recuperar esse resultado; outro id de artefato impede. Upload e referência são
requisições diferentes mesmo quando os bytes coincidem.

## Admissão, concorrência e falhas

O dono reserva o id antes de preparar a entrada, preservando cancelamento nessa
etapa. O digest validado é selado antes da execução. Um retry prepara sua própria
identidade, aguarda o selo se necessário, compara e só então aguarda o resultado.
Divergências não alteram nem cancelam o dono.

Apenas o dono adquire uma vaga de execução. A espera pela identidade ou resultado
usa notificações assíncronas, deixando o pool de workers disponível para hash,
execução e cancelamento. Finalizar ou liberar acorda ambos os tipos de espera,
inclusive quando a preparação falha antes de existir um digest.

Recusa anterior à entrega ao executor ou armazenamento libera id e vaga: por
exemplo parâmetros inválidos, storage de entrada indisponível ou capacidade
esgotada. Retries associados antes de selar a identidade podem receber a recusa
de preparação independentemente da entrada. Após liberar o id, uma requisição
posterior pode se tornar dona e precisa de nova vaga. Após a entrega, sucesso ou
falha são retidos porque pode ter ocorrido trabalho. Retry idêntico recebe esse
resultado; retry alterado é
recusado. Cancelamento mantém a semântica cooperativa e o id terminal existentes;
não transforma operação cancelada em novo trabalho.

A transição terminal sob o lock determina a resposta do dono e dos retries:
cancelamento que vence retorna `409 operation_cancelled` e não retém resultado;
sucesso consolidado sobrevive a cancelamento posterior. Isso não desfaz
publicações em storage/cache já iniciadas antes do cancelamento.

Interrupção da coroutine HTTP é distinta de cancelamento lógico explícito.
Chamadas bloqueantes do request terminam antes de o dono fechar seus streams ou
liberar a vaga, inclusive sob chamadas repetidas de `asyncio.Task.cancel()`.
A exceção de controle original continua chegando ao runtime do dono; retries
recebem um erro tipado existente, sem herdar essa exceção. Preparação interrompida
libera o id com `503 provider_unavailable`; após entrega ao executor/storage, o
resultado incerto é retido como `502 provider_execution_failed`. Cancelamento
lógico explícito prevalece. Encerramento de todo o processo continua encerrando
a garantia local ao processo.

## Limites e responsabilidades do consumidor

Toda tentativa de upload com id é verificada por hash, inclusive retries, para
confirmar os bytes realmente fornecidos. A **passagem de hash** lê blocos positivos
de até 1 MiB, para após o limite REST existente mais um byte e reposiciona o stream.
Acrescenta uma passagem sequencial sem nova cópia integral em memória nem download
de artefato. Se `UploadFile.size` for desconhecido, a verificação inicial de tamanho
também lê o stream separadamente e pode ultrapassar o limite em até um bloco de
1 MiB. O hashing não consulta cancelamento lógico entre blocos: ele é observado
após essa etapa delimitada. Limites HTTP/spooling existentes continuam aplicáveis;
a junção não limita requisições HTTP recebidas, preparação concorrente ou número
de waiters. Entradas terminais são limitadas por quantidade, sem limite de bytes
ou TTL; acompanhamento de orçamento de recursos em
[#182](https://github.com/A11yDevs/acessilia-toolbox/issues/182).
A vinculação é separada das chaves de cache/proveniência e não muda seus formatos.

A correspondência só existe enquanto a réplica dona retém o registro. Expulsão,
reinício e réplicas independentes continuam fora dessa garantia; não há
idempotência distribuída durável. Chamadores devem repetir a requisição original
imutável, usar novo id para trabalho alterado e validar contrato, proveniência e
aplicabilidade da resposta antes de aplicar resultados. Consumidores de
[normalização de texto](text-normalization.pt-br.md) ainda verificam fingerprints
por alvo e a revisão atual do documento.
