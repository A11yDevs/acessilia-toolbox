# B08 — O envio é confirmado, mas outra réplica não encontra o arquivo

## Descrição breve

Quando o MinIO/S3 ficava indisponível, a Toolbox salvava os arquivos no disco
local do servidor que recebia o envio e confirmava sucesso. Outra réplica da
Toolbox, com disco independente, não conseguia recuperar esses arquivos.

O comportamento foi reproduzido em teste isolado. Ele depende de réplicas com
armazenamento local independente; não é um incidente confirmado em produção.

## Sintomas

- O envio é aceito, mas o processamento ou download posterior falha quando
  atendido por outra réplica.
- Durante a queda do armazenamento central, a outra réplica retorna HTTP 503.
- Depois que o MinIO volta, essa réplica pode retornar HTTP 404, pois o arquivo
  nunca foi salvo no armazenamento central.
- O acesso ao arquivo depende de qual réplica recebe a solicitação.

## Solução implementada

**Usar exclusivamente o MinIO/S3 quando houver armazenamento central configurado.**
A aplicação deixa de salvar arquivos localmente como alternativa durante falhas.

- Durante uma indisponibilidade, o envio retorna HTTP 503 com orientação para
  tentar novamente depois, sem confirmar sucesso nem devolver um identificador de arquivo.
- Uma configuração central inválida ou incompleta impede a inicialização,
  em vez de selecionar armazenamento local silenciosamente.
- Após a recuperação, uma nova tentativa bem-sucedida salva o arquivo no
  armazenamento central. Outra réplica com o mesmo endpoint e bucket pode recuperá-lo.
- Configurações explícitas que usam somente filesystem continuam disponíveis
  para desenvolvimento local.
- Não é necessário NFS nem uma nova dependência de execução. Os identificadores
  dos arquivos e os esquemas públicos permanecem iguais.

A consequência é explícita: novos envios não podem ser concluídos enquanto o
armazenamento central estiver indisponível. A solução segue a responsabilidade
central de armazenamento descrita na
[ADR-0006](https://github.com/A11yDevs/acessilia/blob/release/0.2.0/docs/adr/0006-availability-and-failover.md).

**Validação:** quatro casos de regressão falharam antes da correção da seleção.
Depois dela, passaram **1.032 testes unitários** e **88 testes direcionados**, sem
pulos. Há sobreposição entre as suítes; os números não devem ser somados.
Lint, formatação e checagem de tipos dos arquivos de aplicação e armazenamento
alterados passaram. Os testes de integração usam duas aplicações REST e um
servidor HTTP local simulando S3; não houve teste com MinIO real nem em produção.

**Implantação:** preparada para revisão, ainda não implantada. Os arquivos locais
antigos e seus metadados precisam de backup e migração antes da atualização.
Os volumes antigos foram preservados. Consulte o
[procedimento de migração](artifacts.md#migrating-existing-fallback-artifacts).

Referência: Toolbox `release/0.2.0`, base `8f33ea6f165657594433684da6f650065bc46a57`;
auditoria `1a1a866b7a43995f1b1d46f599dd69a0715b69b6`. Data: 26/10/2026.
