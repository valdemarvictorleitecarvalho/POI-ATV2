# Aplicação e características

A aplicação é uma API HTTP que, a cada requisição, aloca um bloco de memória de tamanho informado pelo usuário (`sizeMb`), escreve em todas as suas páginas, o retém por `holdMs` milissegundos e depois faz acessos aleatórios a ele (`passes` passadas). Os acessos são pseudoaleatórios e um por linha de cache, então o prefetcher da CPU não ajuda e a CPU passa boa parte do tempo esperando a RAM. Isso reproduz o perfil **memory-bound**: o dado precisa estar na memória para ser processado, e o recurso que limita é a *capacidade* e a *velocidade* da RAM.

A alocação escreve em todas as páginas de propósito. Um vetor recém-criado é só memória virtual zerada e o sistema operacional só a materializa no primeiro uso; sem essa escrita, a memória residente não refletiria o tamanho pedido. O bloco é liberado explicitamente ao fim da requisição, para que a memória residente reflita as requisições em voo e não o momento em que o coletor de lixo decide rodar.

A retenção é assíncrona. Enquanto uma requisição espera, o worker atende outras, e cada uma segura o seu próprio bloco. Assim, a memória em uso cresce com a quantidade de requisições simultâneas: aproximadamente `sizeMb` vezes o número de requisições em voo. Quando essa soma passa da RAM da máquina virtual, o sistema operacional começa a usar swap, o tempo de resposta degrada bruscamente ou o processo é encerrado pelo OOM killer. Os acessos aleatórios, por outro lado, rodam de forma síncrona no worker e o ocupam até terminar; o tempo deles cresce com `sizeMb` e `passes`.

Na subida, o processo principal cria um worker Node para cada CPU que o sistema operacional vê, como nos outros perfis. Mais vCPUs permitem mais acessos em paralelo, mas a memória usada soma entre os workers; por isso, aumentar a RAM da VM é o que muda o ponto de saturação. Dá para fixar outra quantidade de workers com `WEB_CONCURRENCY`, usar um único processo com `CLUSTER=false` ou limitar a memória em voo por processo com `MAX_INFLIGHT_MB`.

A aplicação é **stateless**: nenhuma informação sobrevive à requisição, e os blocos são descartados ao final dela. Isso facilita o provisionamento horizontal, e o recurso a ser dimensionado é a RAM por réplica.

Para os experimentos, a demanda entra por HTTP, de fora da máquina virtual. O tempo de resposta e as requisições por segundo saem da ferramenta de carga. A memória e a CPU da VM saem das ferramentas do sistema operacional (por exemplo `free`, `vmstat` e `top`) coletadas durante cada teste, e o `rssMb` e o `/stats` mostram o lado da aplicação. A demanda é controlada pela quantidade de conexões simultâneas e pelo `sizeMb`: o ponto de interesse é aquele em que `conexões × sizeMb` ultrapassa a RAM provisionada.

## Por que este desenho e por que estes limites

Na aula de "Aplicações e Características", o perfil **memory-bound** é definido como o da aplicação limitada pela capacidade da RAM, que precisa do dado em memória para processá-lo e cuja CPU fica ociosa esperando esse dado. O exemplo dado é um programa que guarda todo o dado na memória para só depois processar. A rota `/memory` é a versão controlável disso: `sizeMb` é a quantidade de dado mantido em memória e os acessos aleatórios são o processamento. Os outros perfis seguem a mesma lógica com um parâmetro de tamanho (o `limit` do CPU-bound e o `sizeKb` do IO-bound).

**Por que não é CPU-bound nem IO-bound.** Não há cálculo pesado: cada acesso é uma leitura e uma escrita simples, e o tempo vai para esperar a RAM. Também não há disco: o bloco só existe em memória. Se o bloco fosse pequeno, caberia na cache da CPU e a aplicação viraria CPU-bound; por isso o padrão é 64 MB, bem acima da cache de qualquer processador comum.

**Por que a memória é o limite.** O que degrada a aplicação é a soma dos blocos em voo, e não o tamanho de uma requisição só. Por isso existe o `holdMs`: sem retenção, cada requisição liberaria o bloco quase na hora e a memória em uso ficaria baixa mesmo com muita demanda. Com a retenção, a demanda (conexões simultâneas) se traduz em memória ocupada, e o ponto em que `conexões × sizeMb` passa da RAM da VM é onde o desempenho cai: o sistema começa a usar swap ou o processo é morto pelo OOM killer. Esse ponto muda quando a RAM provisionada muda, e é isso que os cenários de provisionamento devem mostrar.

**Por que estes valores.**

- `sizeMb` de 1 a 512 (padrão 64): o máximo foi escolhido para que uma requisição sozinha caiba mesmo numa VM pequena; a pressão de memória deve vir da concorrência, não de um pedido gigante. O mínimo e o padrão ficam fora da cache da CPU.
- `holdMs` de 0 a 10.000 (padrão 500): 0 permite medir o custo de alocar e acessar sem sobreposição; valores maiores aumentam a sobreposição entre requisições e, com ela, a memória em uso.
- `passes` de 1 a 16 (padrão 1): regula quanto tempo a requisição gasta nos acessos, sem mudar a memória usada.
- `MAX_INFLIGHT_MB`: não faz parte do perfil. É uma proteção para a VM não travar em testes agressivos, e fica desligada por padrão. Ao medir a saturação de memória, ela deve ficar desligada, senão as respostas 503 mascaram o efeito.
