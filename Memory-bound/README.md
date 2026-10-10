# Backend Memory-bound

API HTTP em NestJS. A rota de trabalho aloca um bloco de RAM, escreve em todas as páginas, retém o bloco por um tempo e faz acessos aleatórios a ele. A memória usada cresce com a quantidade de requisições em voo (*in flight*), e o tempo de cada requisição é dominado pela espera da RAM, não por cálculo.

Há um processo Node para cada CPU visível, como nos outros perfis. Aqui, o que limita é a RAM da máquina: quando as requisições em voo somam mais do que a memória disponível, o sistema passa a usar **swap** ou o processo é morto pelo **OOM killer**.

> Para entender de onde veio o desenho e por que os limites são esses, veja `CHARACTERISTICS.md`.

## Executar

> Requer Node.js 22 ou superior.

```bash
npm install
npm run build
npm run start:prod
```

Em desenvolvimento: `npm run start:dev`.

A aplicação escuta em `0.0.0.0:3000`.

## Rotas

- `GET /` — o que a aplicação faz e os limites aceitos
- `GET /health` — processo no ar, sem uso de memória
- `GET /stats` — memória do processo que atendeu (`rssMb`, `arrayBuffersMb`, `inflightMb`) e memória livre do sistema
- `GET /memory?sizeMb=64&holdMs=500&passes=1` — aloca `sizeMb` MB, retém por `holdMs` ms e percorre o bloco `passes` vezes; `sizeMb` de 1 a 512 (padrão 64), `holdMs` de 0 a 10000 (padrão 500), `passes` de 1 a 16 (padrão 1)

A resposta de `/memory` traz o tempo de alocação (`allocMs`), de retenção (`heldMs`), de acessos (`accessMs`), o total (`elapsedMs`), quantos MB já estavam em voo no processo quando a requisição chegou (`inflightAtStartMb`), o `rssMb` e o `pid` de quem atendeu.

`/stats` responde pelo worker que pegou a requisição. Com mais de um worker, o valor é de um processo só; para a memória da máquina inteira, use as ferramentas do sistema (por exemplo o comando `htop` no terminal bash do sistema).

## Variáveis

- `PORT` — porta (padrão 3000)
- `CLUSTER=false` — um único processo
- `WEB_CONCURRENCY` — quantidade de workers, se for diferente do número de CPUs
- `MAX_INFLIGHT_MB` — teto de MB em voo por processo; acima dele a rota responde 503. Padrão 0, sem teto. Serve para proteger a VM do OOM killer em testes agressivos

## Teste rápido

```bash
curl "localhost:3000/memory?sizeMb=128&holdMs=2000"
curl localhost:3000/stats
```

Com `CLUSTER=false`, oito requisições simultâneas de 128 MB e `holdMs=2000` levam o `inflightMb` a 1024 e o RSS do processo a cerca de 1,1 GB. Terminadas as requisições, o RSS volta ao nível de repouso.
