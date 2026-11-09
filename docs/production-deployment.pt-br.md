# Implantação da Toolbox em produção

A produção usa `docker-compose.production.yml`, nunca o Compose ou o atualizador
de homologação. O diretório padrão é `/opt/acessilia/toolbox`; o core mantém
configuração e dados em `/opt/acessilia/core`. O projeto Compose continua sendo
`acessilia-toolbox-production`, preservando os volumes existentes ao adotar essa
configuração na VM1. A versão inglesa deste documento é a referência normativa.

## Configuração e isolamento

- `.env` contém os segredos de execução. `TOOLBOX_API_KEY` deve corresponder à
  chave do core. `VLM_API_KEY` deve corresponder à **TOOLBOX_API_KEY do gateway**
  na máquina do modelo, não à chave usada pelo gateway para acessar o modelo.
  Gere credenciais próprias de produção; não copie as da Toolbox ou MinIO de
  homologação.
- `.deploy.env` registra o digest aprovado de cada imagem. O exemplo inicial
  registra o conjunto validado na migração da VM1 para 0.2.0 (Toolbox no commit
  `34802e44109f0324d16428d780dbedee0f7984ab`). É uma referência explícita, não
  uma instrução para acompanhar tags ou reconstruir todas as imagens.
  A cada troca de digest, registre no inventário a revisão de origem, o digest
  implantado de cada imagem, a tag da release, seu digest publicado, a data da
  aceitação e as evidências dos testes. A referência `34802e4` e a futura imagem
  do merge/tag `v0.2.0` são artefatos distintos; não declare equivalência sem
  evidências registradas. Deixe o digest da tag vazio até consultá-lo.
- O registro de produção omite Nougat e Audiveris, que não eram usados. Os
  serviços mantidos têm nomes `*-production`. Nenhuma porta é publicada no host,
  incluindo Toolbox, sidecars, MinIO e Valkey.
- Somente a Toolbox participa da rede externa `acessilia-production-backplane`,
  com o alias `toolbox-production`. Para outra rede, ajuste
  `PRODUCTION_TOOLBOX_NETWORK` no `.deploy.env` de acordo com o core. O core usa
  `http://toolbox-production:8002`. Providers, cache e armazenamento ficam na
  rede privada do projeto.
- A URL VLM deve apontar para `/v1/vlm` do gateway autenticado. Na máquina do
  modelo, mantenha o modelo em loopback e apenas um processo do gateway com
  `VLM_MAX_CONCURRENCY=2`, correspondente aos dois slots do modelo. O limite local
  de quatro da Toolbox não substitui esse limite global compartilhado.

## Preparar sem iniciar serviços

Como usuário de implantação com acesso ao Docker, em um checkout revisado:

```bash
umask 077
mkdir -p /opt/acessilia/toolbox
# Somente instalação nova: não sobrescreva arquivos ou credenciais existentes.
cp -n docker-compose.production.yml /opt/acessilia/toolbox/
cp -n deploy/providers-config.default.yaml /opt/acessilia/toolbox/providers-config.yaml
cp -n deploy/production/runtime.env.example /opt/acessilia/toolbox/.env
cp -n deploy/production/deploy.env.example /opt/acessilia/toolbox/.deploy.env
chmod 600 /opt/acessilia/toolbox/.env /opt/acessilia/toolbox/.deploy.env
```

Preencha os segredos e `VLM_SERVE_URL` em privado. Confira os digests com o
inventário aprovado. Em uma instalação existente, faça backup e compare os
arquivos com os modelos; preserve chaves, configuração de providers, nomes dos
volumes, digests e projeto Compose. O pacote externo usado na migração da VM1 é
a referência para esses arquivos versionados, não outra instalação para iniciar
em paralelo. A adoção é uma mudança manual separada; este PR não altera os
containers ou timers em execução.

```bash
./scripts/production-update.sh --check
```

O preflight resolve o Compose real e rejeita tags, nomes incorretos, segredos
ausentes, URLs diretas do modelo, portas publicadas, alias ausente e credenciais
de implantação injetadas na Toolbox. Não comprova a validade das chaves nem a
conectividade. Não inicia serviços. Não publique o JSON resolvido do Compose,
pois ele contém credenciais.

Crie a rede externa uma vez, se ainda não existir:

```bash
docker network inspect acessilia-production-backplane >/dev/null 2>&1 ||
  docker network create --driver bridge acessilia-production-backplane
```

## Promoção explícita e aceitação

O wrapper antigo acompanhava `main` pelo atualizador de homologação e procurava
um container que aquele Compose não criava. Ele foi substituído por promoção
manual por digest: **sem argumentos, agora apenas verifica**. Desabilite qualquer
timer antigo de produção antes da adoção; este fluxo não instala nem habilita
timers. `PRODUCTION_DIR` permite selecionar outro diretório. O gateway é
implantado separadamente e nunca é atualizado por esses comandos.

1. Suspenda novas submissões em todos os ambientes que usam essa Toolbox e
   aguarde os trabalhos em fila **e em execução**. Fila zero não basta.
2. Faça backup consistente da configuração, do inventário anterior completo de
   digests e dos volumes persistentes. Preserve as imagens antigas. Dimensione
   disco e RAM com documentos reais; o primeiro uso pode baixar modelos.
3. Revise o conjunto de imagens no `.deploy.env` e as alterações de configuração.
4. Execute `./scripts/production-update.sh --apply`. O comando valida, baixa o
   conjunto fixado, executa o Compose com `--wait` somente para serviços
   permanentes (o inicializador é tratado pela dependência de conclusão),
   confere os IDs das imagens e
   a saúde dos serviços, Docling, MinerU e a cadeia VLM autenticada.
5. Teste um documento representativo em fusion dual, exportações, interfaces,
   e-mail, links preservados e rejeição de links expirados antes de liberar o
   tráfego. Saúde não substitui aceitação funcional.

O CI testa esse comando de ativação em containers reais descartáveis com
Compose v5.3.1, a versão informada na VM1: inicialização com sucesso, ativação
repetida e falha do inicializador. Isso verifica o ciclo de vida do Compose;
não substitui o `--apply` completo e a aceitação de documentos com o conjunto
aprovado de imagens. Os erros identificam o serviço/inspeção e são enviados a
stderr; o stderr da resolução do Compose permanece oculto por poder conter
credenciais.

Falha no download preserva os containers em execução. Falha na ativação ou no
teste posterior pode deixar **containers novos rodando**: mantenha o tráfego
suspenso e faça diagnóstico e rollback manual. Não há restauração automática,
cache de sucesso, limpeza de imagens ou exclusão de volumes. Para rollback,
restaure configuração e imagens aprovadas anteriormente e, se necessário, dados
persistentes compatíveis do backup offline. Verifique as dependências antes de
liberar tráfego. Nunca reabra acesso direto ao modelo como atalho de rollback.

## Migração do sidecar para MinerU 4

O adaptador da Toolbox agora chama `/v1/file_parse` e `/v1/health` do MinerU 4
e espera `mineru-kit api-server --tier basic`. Um sidecar MinerU 2.1 não atende
essas rotas. O registro usa `/v1/health` em vez de `/openapi.json`: o MinerU 4
ainda pode expor o documento OpenAPI quando `MINERU_API_ENABLE_FASTAPI_DOCS`
está habilitado, mas documentação opcional não é sinal de protocolo ou de
prontidão. Promova juntos a imagem da Toolbox,
`deploy/providers-config.default.yaml` e `docker-compose.production.yml`.

- Re-pin `PRODUCTION_MINERU_IMAGE` para o digest da imagem CPU MinerU 4.0.12
  aprovada para produção. Não reutilize o digest baseline da 0.2.0: ele é
  MinerU 2.1.
- O novo cache usa `mineru-models:/home/mineru/.mineru` e o cache do Hugging
  Face. O conteúdo antigo do ModelScope não é usado; mantenha-o até o fim da
  janela de rollback e provisione disco e rede, pois a primeira inicialização
  baixa novamente os modelos do MinerU 4.
- O serviço Compose executa `mineru-kit api-server --tier basic`, usa
  `MINERU_MODEL_SMALL_BACKEND=onnx` em CPU e verifica `/v1/health`.
- Faça a aceitação normal com tráfego suspenso para extração de estrutura,
  layout e OCR, incluindo uma faixa parcial de páginas. Saúde sozinha não prova
  que os modelos carregaram.
- O rollback exige a imagem anterior da Toolbox, o sidecar MinerU anterior e o
  registro de provedores anterior juntos. Restaurar apenas um lado deixa o
  protocolo incompatível.

## Homologação e encerramento do BHS

A API autenticada da Toolbox continua acessível pelo core; portas auxiliares
agora escutam em `127.0.0.1` para diagnóstico local. Containers usam o DNS do
Compose para acessar sidecars, MinIO e Valkey. Para diagnóstico remoto, use
túnel SSH, em vez de publicar serviços sem autenticação em todas as interfaces.

Mescle tanto a release da Toolbox em `main` quanto o PR de sincronização em
`develop` com **Create a merge commit**, nunca squash/rebase. Antes de criar a
tag ou excluir branches, a limpeza confere se o head atual da release está nas
duas branches e no merge registrado de `main`; PRs antigos mesclados não provam
que correções posteriores chegaram. Sincronize as correções antes de encerrar a
release do core. Mude a homologação para
`develop` **antes** de `bhs finish --cleanup` excluir as branches release/sync.
A criação de tags e publicação de imagens não promove produção: mantenha os
digests aprovados até uma aceitação e promoção manual separada. Resolva conflitos
na `chore/sync-release-0.2.0`; nunca mescle `develop` na release. As correções
adicionadas durante o encerramento também precisam chegar à branch de
sincronização pelos PRs revisados de correção da release.
