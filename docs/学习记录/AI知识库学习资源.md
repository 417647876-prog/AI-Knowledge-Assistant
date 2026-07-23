# AI 知识库后端学习资源

## Knowledge

- [FastAPI 官方教程](https://fastapi.tiangolo.com/tutorial/)
  用于理解路由、依赖注入、文件上传和异步 API；可对照 ASP.NET Core Controller 与 DI。
- [SQLAlchemy 2.0 官方文档](https://docs.sqlalchemy.org/en/20/)
  用于学习 ORM、Session、事务和 asyncio；重点记住一个异步任务使用一个 `AsyncSession`。
- [pgvector-python 官方仓库](https://github.com/pgvector/pgvector-python)
  用于学习 `VECTOR` 字段以及余弦距离、L2 距离等向量查询。
- [OpenAI Embeddings API](https://platform.openai.com/docs/api-reference/embeddings)
  用于理解文本如何转换为浮点向量，以及向量维度为何必须与数据库字段一致。

## Wisdom (Communities)

- [FastAPI GitHub Discussions](https://github.com/fastapi/fastapi/discussions)
  用于查询真实项目中的依赖注入、异步数据库和部署问题。
- [LangChain GitHub Discussions](https://github.com/langchain-ai/langchain/discussions)
  用于了解 RAG 组件实践；本项目仍保持自己的业务边界，不把核心流程交给框架隐藏。
