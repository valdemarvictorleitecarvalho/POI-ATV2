# Backend IO-bound

API HTTP em NestJS. A rota de trabalho grava um arquivo em disco em blocos, chamando `fsync` após cada bloco, e apaga o arquivo no fim. O tempo de cada requisição é dominado pela espera do disco; a CPU fica quase ociosa.

Há um processo Node para cada CPU visível, como no backend CPU-bound. Aqui, mais vCPUs não aumentam a vazão: o limite é o disco.

## Executar

```bash
npm install
npm run build
npm run start:prod
```

Em desenvolvimento: `npm run start:dev`.

A aplicação escuta em `0.0.0.0:3000` e grava em `./data` (criada na subida).

## Rotas

- `GET /` — o que a aplicação faz e os limites aceitos
- `GET /health` — processo no ar, sem IO
- `GET /io?sizeKb=256&syncs=4` — grava `sizeKb` KB divididos em `syncs` blocos, com `fsync` após cada um; `sizeKb` de 4 a 8192 (padrão 256), `syncs` de 1 a 64 (padrão 4)

A resposta de `/io` traz os bytes gravados, o tempo total no processo (`elapsedMs`), o tempo esperando `fsync` (`syncMs`) e o `pid` de quem atendeu.

## Variáveis

- `PORT` — porta (padrão 3000)
- `DATA_DIR` — diretório dos arquivos temporários (padrão `./data`); precisa estar no disco da VM, não em tmpfs
- `CLUSTER=false` — um único processo
- `WEB_CONCURRENCY` — quantidade de workers, se for diferente do número de CPUs
- `UV_THREADPOOL_SIZE` — threads de IO de arquivo por processo (padrão do Node: 4)

## Experimentos

Em `experiments/vm/`, a aplicação roda na VM do VirtualBox, a carga é gerada no
hospedeiro e o recurso provisionado que varia é a banda de escrita em disco,
aplicada com `VBoxManage bandwidthctl`. São quatro cenários: três variam só a
banda (5, 10 e 20 MB/s) com 1 vCPU e 1 GB, e o quarto dobra vCPUs e memória
mantendo 10 MB/s, para mostrar que CPU e RAM extras não mudam o resultado neste
perfil. Cada cenário passa por cinco níveis de concorrência (1, 2, 4, 8, 16;
25 s cada).

```bash
npm install && npm run build
export VM_SSH_PASSWORD=osboxes.org
cd experiments/vm
python3 run_stress_vm.py
python3 plot_results.py
```

- `run_stress_vm.py` — cenários, provisionamento da VM, carga e coleta (roda no hospedeiro)
- `vm_monitor.py` — amostra a cada segundo CPU, iowait, processos bloqueados, escrita em disco e RSS (roda na VM)
- `plot_results.py` — gráficos de vazão, latência p95, CPU/iowait, disco e memória

Os pré-requisitos e a leitura dos resultados estão em `experiments/vm/README.md`.
O cache de IO do hospedeiro precisa estar desligado, senão o `fsync` para na
memória do hospedeiro e a medição perde o sentido.

## Docker

Imagem publicada no Docker Hub: [`jpedro8azevedo/poi-io-bound`](https://hub.docker.com/jpedro8azevedo/poi-io-bound) (tags `1.0` e `latest`, para `linux/amd64` e `linux/arm64`).

### Build local

```bash
cd IO-bound
docker build -t poi-io-bound:dev .
```

### Executar

A aplicação escuta na porta 3000 do container e grava os arquivos de teste em `/data` (`DATA_DIR`). Para os `fsync` irem para um disco do host, monte uma pasta em `/data`:

```bash
docker run --rm -p 3000:3000 -v "$PWD/data:/data" poi-io-bound:dev
```

Teste:

```bash
curl http://localhost:3000/health
curl "http://localhost:3000/io?sizeKb=256&syncs=4"
```

| Variável | Padrão | Uso |
| --- | --- | --- |
| `PORT` | `3000` | Porta da aplicação dentro do container |
| `DATA_DIR` | `/data` | Pasta onde cada requisição grava e apaga o arquivo |
| `NODE_ENV` | `production` | Modo de execução do Nest |

O container tem `HEALTHCHECK` em `GET /health`, então `docker ps` mostra `healthy` quando a aplicação responde.

### Publicar (amd64 e arm64)

```bash
docker buildx build --platform linux/amd64,linux/arm64 \
  -t jpedro8azevedo/poi-io-bound:1.0 -t jpedro8azevedo/poi-io-bound:latest --push .
```

### Deploy na vm-grupo1

No `docker-compose.yml` da raiz (branch `docker/compose`), o serviço `io-bound` usa a imagem publicada, expõe a porta `4002` da VM e liga `/data` à pasta `/mnt/dados/io-docker`, no volume do Cinder. Pelo balanceador, a aplicação fica em `http://150.165.85.77:9003`.