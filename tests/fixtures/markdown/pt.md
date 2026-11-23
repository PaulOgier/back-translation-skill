---
title: "Como escolher uma senha forte"
description: Qual deve ser o tamanho de uma senha e por que 15 caracteres é um bom começo.
lang: pt-BR
---

# Como escolher uma senha forte

As senhas devem ter pelo menos 15 caracteres[1].

Você pode usar um *gerenciador de senhas* para guardá-las.

## O que verificar

- Use uma senha diferente para cada site.
- Ative a verificação em duas etapas.
  - Prefira um aplicativo a mensagens de texto.
- Troque a senha depois de um vazamento.

| Tamanho | Tempo para adivinhar |
|---|---|
| 8 caracteres | 2 dias |
| 15 caracteres | 400 anos |

Execute `pwgen -s 15` para criar uma, ou leia [o guia][guia].

> Uma frase-senha longa é mais fácil de lembrar do que uma senha curta e complexa.

```sh
pwgen -s 15 1
```

![Gráfico do tempo para adivinhar](/img/chart.png)

[guia]: https://example.com/guide "Guia de senhas"
