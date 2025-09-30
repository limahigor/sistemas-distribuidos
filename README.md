# Gerenciamento de prontuários baseado em microserviços

Este projeto é um sistema de exemplo baseado em uma arquitetura de microserviços, desenvolvido com o objetivo de aprender e praticar conceitos de sistemas distribuídos.

## Pré-requisitos

Antes de começar, você vai precisar ter instalado em sua máquina:
* [Python 3.8+](https://www.python.org/downloads/)
* [Docker](https://www.docker.com/products/docker-desktop/)
* [Docker Compose](https://docs.docker.com/compose/install/)

## Como Executar o Projeto

Siga os passos abaixo para configurar e executar a aplicação.

### 1. Preparação do Ambiente Local

Primeiro, clone o repositório e configure o ambiente virtual Python para instalar as dependências necessárias.

```bash
# Clone este repositório
git clone [https://github.com/limahigor/sistemas-distribuidos.git](https://github.com/limahigor/sistemas-distribuidos.git)

# Acesse a pasta do projeto
cd sistemas-distribuidos

# Crie um ambiente virtual
python -m venv venv

# Ative o ambiente virtual

# No Windows:
venv\Scripts\activate

# No Linux ou macOS:
source venv/bin/activate

# Instale as dependências do projeto
pip install -r requirements.txt
```

### 2. Executando os Serviços

Todos os serviços são orquestrados com Docker Compose. Para iniciá-los, navegue até a pasta `infra` e execute o comando `up`.

```bash
# Navegue até a pasta de infraestrutura
cd infra

# Suba todos os serviços
docker compose up --build -d
```

Após a execução do comando, todos os microserviços estarão em execução em segundo plano.

---
