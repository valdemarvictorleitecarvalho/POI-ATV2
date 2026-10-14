# Experimentos do IO-bound na VM

A aplicação roda na VM do VirtualBox (`cav_vm`), a carga é gerada no hospedeiro
e o recurso provisionado que varia é a banda de escrita em disco, aplicada por
um grupo de largura de banda do VirtualBox.

Quatro cenários: 5, 10 e 20 MB/s com 1 vCPU e 1 GB, mais 10 MB/s com 2 vCPU e
2 GB. Cada um passa por cinco níveis de concorrência (1, 2, 4, 8, 16; 25 s
cada). O gerador de carga nunca roda dentro da VM, para não disputar disco com
a aplicação.

## Antes de rodar

1. VM `cav_vm` existente, com redirecionamento SSH `2222 -> 22`. A regra da
   porta 3000 é criada pelo script se faltar.
2. Aplicação compilada e copiada para a VM:

   ```bash
   cd IO-bound && npm install && npm run build
   scp -P 2222 -r dist node_modules package.json osboxes@localhost:/home/osboxes/io-bound-backend/
   scp -P 2222 experiments/vm/vm_monitor.py osboxes@localhost:/home/osboxes/
   ```

3. Node.js 22 na VM, no caminho de `NODE_BIN` (o mesmo usado pelo CPU-bound).
4. `DATA_DIR` no disco da VM, não em `tmpfs`. O script aborta se for `tmpfs`.
5. Cache de IO do hospedeiro desligado. O script faz isso com a VM desligada,
   mas vale conferir: sem isso o `fsync` para na memória do hospedeiro e o
   teste mede RAM, não disco.

## Rodar

```bash
export VM_SSH_PASSWORD=osboxes.org
cd IO-bound/experiments/vm
python3 run_stress_vm.py
python3 plot_results.py
```

No Windows, a variável é `set VM_SSH_PASSWORD=osboxes.org` (cmd) ou
`$env:VM_SSH_PASSWORD="osboxes.org"` (PowerShell), e o acesso por chave SSH é
obrigatório: o `SSH_ASKPASS` do Linux não existe ali, então sem chave o `ssh`
fica esperando senha e o script trava. A variável continua necessária em
qualquer sistema, porque o desligamento da VM usa `sudo -S` dentro dela.

Se o `VBoxManage` não estiver no `PATH`, aponte o caminho:

```bash
export VBOXMANAGE="/caminho/para/VBoxManage"
```

No Windows o script já tenta
`C:\Program Files\Oracle\VirtualBox\VBoxManage.exe` por padrão.

`run_stress_vm.py` desliga e religa a VM entre cenários que mudam vCPUs ou
RAM; para mudar só a banda, altera o limite com a VM no ar. Os resultados vão
para `results.json` a cada cenário concluído, então uma execução interrompida
continua de onde parou: cenário já presente no arquivo é pulado.

Ao final a VM volta para 1 vCPU, 1024 MB e limite de 1000 MB/s, com a
aplicação no ar. Para remover o grupo de banda, com a VM desligada:

```bash
VBoxManage bandwidthctl cav_vm remove iolimit
```

## Arquivos

- `run_stress_vm.py` — cenários, carga do hospedeiro e coleta (roda no hospedeiro)
- `vm_monitor.py` — amostra CPU, iowait, bloqueados, escrita em disco e memória (roda na VM)
- `plot_results.py` — gera `figures/` a partir de `results.json`

## Saída

`results.json` com `environment` e `scenarios`. Cada nível traz, além da vazão
e das latências, `server_elapsed_ms_avg`, `server_sync_ms_avg` e `sync_ratio`
(a fração do tempo da requisição gasta em `fsync`), mais as médias de
`cpu_pct`, `iowait_pct`, `blocked`, `disk_write_mbps`, `mem_used_mb` e
`node_rss_mb`.

`sync_ratio` baixo é o sinal de que o `fsync` não chegou ao disco — quase
sempre cache de IO do hospedeiro ligado.
