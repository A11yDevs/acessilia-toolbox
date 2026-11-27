# Identidade da requisição de operação

Inglês: [English version](operation-identity.md).

Um `operation_id` identifica uma execução lógica dentro de uma tarefa. Reutilizá-lo
retorna a resposta ou erro de execução original apenas quando a requisição
completa corresponde. Requisição validada conflitante recebe o erro existente
`400 invalid_input`; requisições malformadas ou não suportadas mantêm seus erros
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
esgotada. Retries que já aguardavam recebem essa recusa; uma tentativa posterior
pode se tornar dona. Após a entrega, sucesso ou falha são retidos porque pode ter
ocorrido trabalho. Retry idêntico recebe esse resultado; retry alterado é
recusado. Cancelamento mantém a semântica cooperativa e o id terminal existentes;
não transforma operação cancelada em novo trabalho.

## Limites e responsabilidades do consumidor

Toda tentativa de upload com id é verificada por hash, inclusive retries, para
confirmar os bytes realmente fornecidos. Leituras são positivas, de até 1 MiB,
param após o limite REST existente mais um byte e reposicionam o stream do
request. Isso acrescenta uma passagem sequencial por tentativa, sem nova cópia
integral em memória nem download de artefato. Limites HTTP/spooling existentes
continuam aplicáveis; a junção não limita o número de requisições HTTP recebidas.
A vinculação é separada das chaves de cache/proveniência e não muda seus formatos.

A correspondência só existe enquanto a réplica dona retém o registro. Expulsão,
reinício e réplicas independentes continuam fora dessa garantia; não há
idempotência distribuída durável. Chamadores devem repetir a requisição original
imutável, usar novo id para trabalho alterado e validar contrato, proveniência e
aplicabilidade da resposta antes de aplicar resultados. Consumidores de
[normalização de texto](text-normalization.pt-br.md) ainda verificam fingerprints
por alvo e a revisão atual do documento.
