# Plano de polimento da UI (out/2026)

Análise feita por um subagente (Fable) com o app rodando a 1440×900, 760 e 420 px de largura e lendo `web/src`.
A arquitetura de informação é sólida e o tema escuro é consistente. As lacunas são de polimento: estados de carregando
e de erro, diálogos nativos do navegador, toolbar da galeria lotada, acessibilidade de botões só com ícone e
inconsistências entre telas.

## Achados principais

1. **Estados de carregando e de erro.** Hoje são texto cru, sem forma de recuperar. "Carregando…" e o erro em vermelho
   aparecem sem link de volta. Na galeria, a grade fica em branco antes de aparecer o estado vazio.
2. **`window.confirm` e `window.prompt` em 17 lugares.** Quebram o tema e o foco por teclado.
3. **Toolbar da galeria.** Ocupa 3 linhas a 1440 px e 5 linhas a 760 px.
4. **Botões só com ícone sem nome acessível.** O `Tooltip` existe, mas não é usado. Controles que só aparecem no hover
   não podem ser alcançados pelo teclado.
5. **"Gerar" em tela estreita.** A lista de ~30 pipelines enterra o formulário.
6. **Títulos de pipeline misturam inglês e português.**
7. **Abas e segmentos.** São cinco implementações diferentes.
8. **`<select>` nativo.** Estilizado de jeitos diferentes (alturas `h-7`, `h-8` e `h-9`).
9. **Cabeçalhos de página.** Tamanhos, margens e larguras máximas não batem entre as telas.
10. **Validação do formulário de geração.** É fraca ("falta: prompt" pequeno), e as descrições aparecem cortadas.
11. **Progresso dos jobs.** Só é visível na página de jobs.
12. **Rota padrão.** Não existe; uma URL desconhecida mostra a tela em branco.
13. **Painel lateral do asset.** Não separa o que é só leitura do que é editável, e os inputs não têm label.
14. **Controles só no hover.** Checkbox do card, ações de pasta e o pin.
15. **Detalhes menores.**
    - Texto de 10 a 11 px.
    - O subtítulo da Home mostra um caminho de disco.
    - A Home não tem estado vazio.
    - Os toasts cobrem a linha do tempo do editor.
    - Há um emoji no estado vazio.

## Itens de trabalho

- **W1** `PageState`: loading, erro (com Voltar e Tentar de novo) e vazio, em todas as páginas.
- **W2** `ConfirmDialog` e `PromptDialog` (`useConfirm`) no lugar de `window.confirm/prompt`.
- **W3** Toolbar da galeria reagrupada: busca, modalidade, botão Filtros recolhível, "Novo" em um menu e "Importar".
- **W4** `aria-label` e `Tooltip` nos botões só com ícone; controles do hover visíveis com foco.
- **W5** Primitivo `NativeSelect` único.
- **W6** Gerar: seletor de pipeline responsivo, validação clara e descrições sem corte.
- **W7** Indicador persistente de jobs ativos (progresso na barra lateral), com toast "Pronto" e botão Abrir.
- **W8** Abas e segmentos unificados nos Tabs do Base UI.
- **W9** `PageHeader` e grade de layout comum.
- **W10** Painel do asset em seções: Organização, Detalhes (com labels e Salvar só quando há mudança) e Geração.
- **W11** Rota padrão (404) e trilha Projeto › pasta › asset.
- **W12** Títulos de pipeline em português no cliente.
- **W13** Tamanho mínimo de texto de 12 px e toasts no topo.

## Não mexer

- Arquitetura da barra lateral.
- Abas do projeto na URL e `?f=` da pasta.
- Árvore de pastas com arrastar e soltar.
- AssetPicker.
- Cards de job (repetir, variação, editar e repetir).
- Telas de Instalação e de Opções > ComfyUI.
- Invalidação do cache por WebSocket.
- Tokens do tema.
