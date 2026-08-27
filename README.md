# Vocabulary builder

Seleciona diariamente uma palavra do histórico pessoal do DuoCards e gera os
arquivos consumidos pelo KWGT na tela bloqueada.

O pipeline validado é:

```text
DuoCards (palavra + progresso)
        +
Wiktionary (definição + exemplo)
        ↓
word.json / word.txt
        ↓
GitHub raw
        ↓
KWGT → LockStar
```

## O que causava o HTTP 403

O DuoCards mudou o formato de transporte esperado pela API. A consulta antiga
do Duoload enviava o POST para:

```text
https://api.duocards.com/graphql
```

Em 27 de agosto de 2026, esse endereço retorna `403 Forbidden` para o payload
de cards. A mesma consulta, sem token, cookie, `Origin`, `Referer` ou headers de
versão, retorna HTTP 200 quando o nome da operação está na URL:

```text
https://api.duocards.com/graphql?cardsQuery
```

O corpo continua sendo `{ "query": ..., "variables": ... }`. Não é necessário
copiar a sessão do Chrome nem guardar credenciais. O cliente mantém
`?cardsQuery` literalmente e falha de forma explícita se a API mudar novamente.

## Resultado observado na conta

Snapshot de 2026-08-27:

| Métrica | Valor |
|---|---:|
| Cards | 984 |
| Palavras únicas | 984 |
| Páginas GraphQL | 10 |
| Cards com `hint` | 26 |
| Cards com `theoryEn` | 0 |
| Listas/fontes distintas | 9 |

Distribuição de `knownCount`:

| Valor | Cards |
|---:|---:|
| 0 | 1 |
| 1 | 3 |
| 2 | 9 |
| 3 | 3 |
| 4 | 31 |
| 5 | 10 |
| 6 | 41 |
| 7 | 886 |

O frontend atual só incrementa `knownCount` até 7 e considera `>= 7` como
completamente aprendido. Portanto, as faixas antigas `> 25` nunca seriam
selecionadas. Os defaults foram corrigidos para:

- segunda, terça, quinta, sexta e sábado: `knownCount` 5–6;
- quarta e domingo: `knownCount >= 7`, com `hard_day_label = "hard day"`.

O deck reúne palavras oriundas de várias listas, inclusive `the-eaten-heart-boccaccio`.
Isso confirma operacionalmente que o ID usado aqui é o vocabulário pessoal
consolidado do par de idiomas, e não o ID de uma lista compartilhada.

## Por que a definição vem do Wiktionary

Nos 984 cards, `sCard.theory.theoryEn` é nulo. Consultar diretamente as nove
listas-fonte também retornou zero `theoryEn`. Como apenas 26 cards têm `hint`, o
DuoCards sozinho não consegue preencher diariamente definição e exemplo.

O script consulta o endpoint oficial de definições do English Wiktionary apenas
para candidatos elegíveis, até encontrar uma entrada com os dois campos. A
atribuição, a página de origem e a licença CC BY-SA 4.0 ficam no `word.json`.

## Arquivos

```text
.github/workflows/update-word.yml  atualização diária às 04:17 UTC
scripts/duocards_client.py         download paginado e defensivo
scripts/wiktionary_client.py       definição, exemplo e atribuição
scripts/update_word.py             seleção, histórico e escrita atômica
scripts/analyze_cards.py            diagnóstico opcional
data/history.json                  últimas 120 palavras
word.json                          saída principal do KWGT
word.txt                           fallback delimitado por ~
tests/                             testes sem dependências externas
```

Os snapshots completos são privados e estão no `.gitignore`.

## Execução local

Requer Python 3.11 ou superior e não possui dependências externas:

```powershell
python scripts/update_word.py
python -m unittest discover -s tests -v
```

Para analisar o deck e salvar snapshots privados:

```powershell
python scripts/analyze_cards.py `
  --snapshot data/cards_snapshot.json `
  --source-snapshot data/sources_snapshot.json `
  --back-lang pt
```

Uma simulação sem alterar arquivos:

```powershell
python scripts/update_word.py --dry-run
```

As variáveis opcionais são:

- `DUOCARDS_DECK_ID`: sobrescreve o ID padrão;
- `WIKIMEDIA_USER_AGENT`: identificação recomendada pela Wikimedia, idealmente
  com a URL do repositório ou um contato.

## Regras de seleção

1. Normaliza e deduplica a palavra com Unicode NFKC, espaços colapsados e
   `casefold`.
2. Em duplicatas, prioriza definição+exemplo, maior `knownCount`, maior
   completude e ID estável.
3. Exclui as últimas 120 palavras quando existem alternativas.
4. Se o pool for menor, reutiliza primeiro a palavra exibida há mais tempo.
5. A ordem diária é determinística por SHA-256; reexecutar na mesma data preserva
   a palavra e não duplica o histórico.
6. Só substitui os três arquivos depois de baixar, enriquecer e validar tudo. Se
   a rede ou uma API falhar, a versão anterior permanece intacta.

## Saída

Exemplo abreviado:

```json
{
  "word": "deadpan",
  "definition": "Deliberately impassive or expressionless.",
  "example": "a deadpan face or look",
  "translation": "inexpressivo",
  "known_count": 6,
  "hard_day": false,
  "hard_day_label": "",
  "mode": "normal",
  "date": "2026-08-27",
  "source": "DuoCards + Wiktionary"
}
```

`word.txt` contém quatro campos:

```text
word~definition~example~hard day
```

Quebras de linha e `~` internos são neutralizados antes da escrita.

## Fórmulas KWGT

As fórmulas abaixo já usam o usuário GitHub conectado (`paulocantalice`).

Palavra:

```text
$wg("https://raw.githubusercontent.com/paulocantalice/vocabulary-builder/main/word.json?t="+df(yyyyMMddHH), json, ".word")$
```

Definição:

```text
$wg("https://raw.githubusercontent.com/paulocantalice/vocabulary-builder/main/word.json?t="+df(yyyyMMddHH), json, ".definition")$
```

Exemplo:

```text
$wg("https://raw.githubusercontent.com/paulocantalice/vocabulary-builder/main/word.json?t="+df(yyyyMMddHH), json, ".example")$
```

Hard day:

```text
$wg("https://raw.githubusercontent.com/paulocantalice/vocabulary-builder/main/word.json?t="+df(yyyyMMddHH), json, ".hard_day_label")$
```

## Automação e publicação

O workflow roda às `04:17 UTC` (`01:17 America/Sao_Paulo`) e também aceita
execução manual. Só cria commit quando a saída muda.

Há duas ressalvas antes de tornar o repositório público:

1. A GraphQL do DuoCards não é uma API pública documentada. Os Termos atuais não
   autorizam expressamente automação, embora esta implementação não use token nem
   contorne autenticação. Confirme esse uso com o DuoCards antes de operar o cron
   permanentemente.
2. Definição e exemplo são texto do Wiktionary sob CC BY-SA 4.0. Preserve
   `NOTICE.md`, os campos de atribuição do JSON e a licença ao redistribuir.
