# Experimentos do Memory-bound

Scripts que geram a demanda, medem a aplicação e a VM e produzem os gráficos da seção de resultados.

- `run_stress.py` - gera a carga **a partir do hospedeiro** (fora da VM), reconfigura a VM a cada cenário, sobe a aplicação e o coletor e grava `results.json`
- `guest_monitor.py` - roda **dentro da VM**; uma vez por segundo lê CPU, memória, swap e mortes por falta de memória (OOM) em `/proc`
- `plot_results.py` - lê `results.json` e escreve os gráficos em `figures/`
- `requirements.txt` - `pip install -r requirements.txt` (matplotlib e paramiko)

## O que o experimento mede

Cada nível de demanda mantém N conexões simultâneas chamando `GET /memory?sizeMb=64&holdMs=4000&passes=1` durante 30 s. Como cada requisição segura 64 MB enquanto está em voo, a VM precisa de aproximadamente **N × 64 MB** de RAM. O que varia entre cenários é a RAM provisionada (768 MB, 1 GB, 1,5 GB e 2 GB, sempre com 1 vCPU).

Por que 1 vCPU e `holdMs` alto: a alocação e os acessos aleatórios são trabalho de CPU, síncrono no worker. Com retenção curta, a CPU de 1 worker satura em poucas dezenas de req/s antes de a memória apertar, e o experimento mediria CPU. Com 4 s de retenção, cada requisição passa quase todo o tempo parada segurando memória, e o primeiro recurso a acabar é a RAM.

Por requisição, a aplicação devolve `allocMs`, `accessMs`, `elapsedMs` e `inflightAtStartMb`. O script também registra, por nível:

- **cliente (hospedeiro):** requisições ok/erro (503, conexão recusada ou timeout), vazão, latência média, mediana, p95 e máxima
- **aplicação:** médias de `allocMs`, `accessMs` e `elapsedMs`, e os MB já em voo na chegada de cada requisição
- **VM:** CPU média e pico, iowait, memória em uso (média e pico), memória disponível mínima, swap em uso, páginas trazidas e enviadas ao swap, mortes por OOM e RSS dos processos Node

## Estimativa de onde a RAM aperta

Valores aproximados, a partir dos ~400 MB que a VM usou com o CPU-bound e 1 worker (sistema + Node) e da RAM que o Linux enxerga (um pouco abaixo da configurada). São uma hipótese para escolher os níveis, não um resultado.

| RAM da VM | Sobra para blocos | Requisições simultâneas até apertar |
|---|---|---|
| 768 MB | ~400 MB | ~6 |
| 1 GB | ~650 MB | ~10 |
| 1,5 GB | ~1,1 GB | ~18 |
| 2 GB | ~1,7 GB | ~26 |

Os níveis `1, 2, 4, 8, 12, 16, 24, 32` cobrem essas faixas. **Antes da rodada completa, faça uma calibração** (abaixo) e ajuste RAMs e níveis se o ponto de saturação cair fora da faixa.

## Preparação

1. Crie a VM e as regras de redirecionamento conforme o roteiro: SSH `2222 → 22` e aplicação `3000 → 3000`. O script cria as regras que faltarem.
2. Instale as dependências no hospedeiro: `pip install -r requirements.txt`.
3. Defina a senha do SSH (usuário `osboxes`):
   - PowerShell: `$env:VM_SSH_PASSWORD = "osboxes.org"`
   - cmd: `set VM_SSH_PASSWORD=osboxes.org`
   - Linux/macOS: `export VM_SSH_PASSWORD=osboxes.org`
4. No Windows, se `VBoxManage` não estiver no PATH, aponte o executável: `$env:VBOXMANAGE = "C:\Program Files\Oracle\VirtualBox\VBoxManage.exe"`. O script já tenta esse caminho.
5. Instale o Node.js 22 dentro da VM, se ela ainda não tiver (a imagem do roteiro não traz). Entre por SSH (`ssh -p 2222 osboxes@localhost`) e rode:

   ```bash
   cd ~
   wget https://nodejs.org/dist/v22.18.0/node-v22.18.0-linux-x64.tar.xz
   tar -xf node-v22.18.0-linux-x64.tar.xz
   ~/node-v22.18.0-linux-x64/bin/node -v
   ```
6. Faça o deploy da aplicação na VM. No hospedeiro, dentro de `Memory-bound`:

   PowerShell (Windows):

   ```powershell
   npm ci
   npm run build
   New-Item -ItemType Directory deploy
   Copy-Item -Path dist,package.json,package-lock.json -Destination deploy -Recurse
   cd deploy; npm ci --omit=dev; cd ..
   scp -P 2222 -r deploy osboxes@localhost:/home/osboxes/memory-bound-backend
   ```

   Linux ou macOS:

   ```bash
   npm ci && npm run build
   mkdir deploy && cp -r dist package.json package-lock.json deploy/
   (cd deploy && npm ci --omit=dev)
   scp -P 2222 -r deploy osboxes@localhost:/home/osboxes/memory-bound-backend
   ```

   Apague a pasta `deploy` depois. Por padrão o script procura o Node em `/home/osboxes/node-v22.18.0-linux-x64/bin/node` e a aplicação em `/home/osboxes/memory-bound-backend`. Outros caminhos: `--guest-node` e `--guest-dir`.
7. Para validar o deploy antes dos testes, suba a aplicação à mão na VM (`cd ~/memory-bound-backend && ~/node-v22.18.0-linux-x64/bin/node dist/main.js`) e chame, do hospedeiro, `curl.exe http://localhost:3000/health` e `curl.exe "http://localhost:3000/memory?sizeMb=64&holdMs=500"`. Não precisa encerrar essa instância: o script a reinicia sozinho no primeiro cenário.
8. Dentro da VM, veja se há swap: `free -m` e `swapon --show`. Isso muda o que acontece quando a memória acaba (abaixo).

Os próximos passos são feitos na máquina hospedeira, com conexão SSH a VM.

## Execução

Calibração, com uma RAM só e resultado em arquivo separado:

```bash
python run_stress.py --scenarios 1:1024 --levels 1,4,8,12,16 --duration 15 --results calibracao.json
```

Confira em `calibracao.json`: a vazão cresce com a concorrência até o ponto em que a memória aperta? O `server_access_ms_avg` é o tempo de CPU por requisição; a CPU satura perto de `(holdMs + tempo) / tempo` conexões, e esse número precisa ficar acima do ponto de saturação da RAM. Se a CPU (`cpu_pct_avg`) já estiver perto de 100% antes, aumente `--hold-ms` ou diminua `--size-mb`.

Rodada completa e gráficos:

```bash
python run_stress.py
python plot_results.py
```

Cada cenário é salvo ao terminar. Se a execução for interrompida, rodar de novo pula os cenários prontos; `--force` refaz tudo. Ao final, a VM volta para 1 vCPU e 1 GB (`--restore none` desativa).

Outras opções: `--scenarios 1:768,1:1024` (vCPUs:RAM em MB), `--levels`, `--size-mb`, `--hold-ms`, `--duration`, `--vm`, `--ssh-port`, `--app-port`. Veja `python run_stress.py --help`.

Para medir uma aplicação que já está no ar, sem mexer na VM, use `--mode attach --label nome-do-cenario` (acrescente `--no-guest` para registrar só as métricas do cliente).

## Gráficos

`plot_results.py` escreve em `figures/`:

- `vazao.png` e `latencia_p95.png` - desempenho por concorrência e por RAM
- `taxa_erro.png` - requisições que falharam
- `memoria.png` - pico de memória em uso, com a RAM da VM em linha tracejada
- `swap.png` - pico de swap em uso
- `cpu.png` - CPU média da VM
- `tempo_servidor.png` - tempo médio de alocação e de acessos dentro da aplicação

## Cuidados

- **O que acontece sem memória depende do swap.** Com swap, a VM não morre: ela começa a trocar páginas, o `allocMs` e o `accessMs` disparam, a latência explode e a vazão despenca (veja `swap_in_pages`, `swap_out_pages` e `iowait_pct_avg`). Sem swap, o kernel mata o Node (`oom_kills`), as requisições falham e a aplicação é reiniciada pelo script no nível seguinte.
- **Deixe `--max-inflight-mb` em 0 durante as medições.** O teto responde 503 de imediato, a conexão tenta de novo na hora e a taxa de erro fica inflada por respostas instantâneas.
- **O script para de subir a demanda** num cenário quando a taxa de erro chega a 50% ou a vazão cai abaixo de 10% do pico, para não travar a VM por muito tempo. O nível em que parou fica marcado com `stopped_here` no `results.json`.
- Quando a VM fica sem resposta por causa da pressão de memória, a coleta daquele nível pode falhar. O script continua e grava o nível só com as métricas do cliente.
- O coletor começa cerca de um segundo antes da carga, então a média de CPU de cada nível inclui uma amostra ociosa.
- Se o script avisar que a VM vê um número de CPUs diferente do cenário, a VM não foi reiniciada com a nova configuração.