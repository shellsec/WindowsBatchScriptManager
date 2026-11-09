# WindowsBatchScriptManager · Português (Brasil)

[English](README.md) · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-TW.md) · [日本語](README.ja-JP.md) · [한국어](README.ko-KR.md) · [Deutsch](README.de-DE.md) · [Français](README.fr-FR.md) · [Español](README.es-ES.md) · [Português (Brasil)](README.pt-BR.md) · [Русский](README.ru-RU.md)

Gerencie scripts em lote, Python, Node.js e PowerShell no Windows com agendamento, fila de execução e API remota.

[Baixar para Windows](https://github.com/shellsec/WindowsBatchScriptManager/releases)
## Início rápido

Para usar o aplicativo de desktop, execute o EXE independente. Para a API remota, coloque o EXE e o script da API na mesma pasta do servidor, instale as dependências do Python e inicie o serviço. Preserve os arquivos JSON de configuração ao atualizar.

```powershell
python -m pip install fastapi "uvicorn[standard]"
python WindowsBatchScriptManager_api_server.py --host 0.0.0.0 --port 8765 --api-key "<API_KEY>"
```

A primeira opção do menu de idiomas segue o sistema. A segunda é English. Também é possível escolher o idioma manualmente.

## Limpar a unidade C:

Na v1.3.4, o botão de limpeza fica ao lado do botão que abre a pasta da ferramenta. A limpeza é executada em segundo plano e exibe os resultados no registro na parte inferior. Remove arquivos `.tmp` e `.temp` modificados há mais de sete dias nas pastas temporárias do usuário e do sistema em C:, além do cache de miniaturas do usuário em C:. **Esvazia permanentemente a Lixeira de C: sem confirmação** e limpa o cache DNS. Usa as permissões atuais e preserva o cache do Windows Update, o Prefetch e os logs do sistema.

![Captura de tela](docs/screenshots/pt-BR.jpg)

[API / MCP](docs/USER_GUIDE.en-US.md#remote-api-and-mcp) · [Apache-2.0](LICENSE) · [NOTICE](NOTICE)
