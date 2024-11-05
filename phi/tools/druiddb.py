import json
from typing import Optional
from phi.tools import Toolkit
from phi.utils.log import logger

try:
    from pydruid.db import connect
except ImportError:
    raise ImportError("`pydruid` not installed. Please install using `pip install pydruid` ")


class DruidTools(Toolkit):
    def __init__(
        self,
        host: str,
        port: int = 8888,
        scheme: str = "http",
        path: str = "/druid/v2/sql/",
        list_tables: bool = True,
        describe_table: bool = True,
        run_query: bool = True,
        table_sample: bool = True,
        table_stats: bool = True,
    ):
        super().__init__(name="druid_tools")

        self.connection_params = {"host": host, "port": port, "path": path, "scheme": scheme}

        self.context = {"last_schema": None, "last_tables": [], "last_query": None, "last_error": None}

        self._connect()

        if list_tables:
            self.register(self.list_tables)
        if describe_table:
            self.register(self.describe_table)
        if run_query:
            self.register(self.run_query)
        if table_sample:
            self.register(self.get_table_sample)
        if table_stats:
            self.register(self.get_table_stats)

    def _connect(self) -> None:
        """Function to establish connection with Druid database
        :return: None
        """
        try:
            self.connection = connect(**self.connection_params)
            self.cursor = self.connection.cursor()
            self._test_connection()
        except Exception as e:
            logger.error(f"Failed to connect to Druid: {e}")
            raise

    def _test_connection(self) -> None:
        """Function to test the Druid database connection by executing a simple query
        :return: None
        """
        try:
            self.cursor.execute("SELECT 1")
            self.cursor.fetchone()
        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            raise

    def _execute_query(self, query: str, params: Optional[tuple] = None) -> list:
        """Function to execute SQL query on Druid database
        :param query: SQL query string to execute
        :param params: Optional tuple of parameters for the query
        :return: List of query results
        """
        try:
            self.context["last_query"] = query

            # Handle different parameter cases
            if params and isinstance(params, tuple):
                # For schema_query with table_name parameter
                if "TABLE_NAME = %s" in query:
                    param_value = params[0]  # Extract value from tuple
                    modified_query = query.replace("%s", f"'{param_value}'")
                    self.cursor.execute(modified_query)
                # For column_query with schema and table parameters
                elif "TABLE_SCHEMA = %s AND TABLE_NAME = %s" in query:
                    schema, table = params
                    modified_query = query.replace("%s", f"'{schema}'", 1)
                    modified_query = modified_query.replace("%s", f"'{table}'", 1)
                    self.cursor.execute(modified_query)
                else:
                    self.cursor.execute(query)
            else:
                self.cursor.execute(query)

            results = self.cursor.fetchall()
            self.context["last_error"] = None
            return results
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Query execution failed: {error_msg}")
            self.context["last_error"] = error_msg
            raise

    def list_tables(self, schema_name: str = "") -> str:
        """Function to show available tables in the database
        :param schema_name: Optional schema name to filter tables
        :return: JSON string containing list of tables grouped by schema
        """
        try:
            query = """
                SELECT 
                    TABLE_SCHEMA as schema_name,
                    TABLE_NAME as table_name,
                    TABLE_TYPE as table_type
                FROM INFORMATION_SCHEMA.TABLES
            """
            if schema_name:
                query += f" WHERE TABLE_SCHEMA = '{schema_name}'"
            query += " ORDER BY TABLE_SCHEMA, TABLE_NAME"

            results = self._execute_query(query)

            schemas = {}
            for row in results:
                schema = row[0]
                if schema not in schemas:
                    schemas[schema] = []
                schemas[schema].append({"table_name": row[1], "table_type": row[2]})

            return json.dumps(
                {"status": "success", "schemas": [{"schema": k, "tables": v} for k, v in schemas.items()]}
            )
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})

    def describe_table(self, table_name: str) -> str:
        """Function to describe a specific table
        :param table_name: Name of the table to describe
        :return: JSON string containing detailed table information
        """
        try:
            schema_query = "SELECT TABLE_SCHEMA FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = %s"
            results = self._execute_query(schema_query, (table_name,))
            if not results:
                return json.dumps({"status": "error", "message": f"Table {table_name} not found"})

            schema_name = results[0][0]
            column_query = """
                SELECT 
                    COLUMN_NAME,
                    DATA_TYPE,
                    IS_NULLABLE,
                    COLUMN_DEFAULT
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                ORDER BY ORDINAL_POSITION
            """
            columns = self._execute_query(column_query, (schema_name, table_name))

            return json.dumps(
                {
                    "status": "success",
                    "table_info": {
                        "schema": schema_name,
                        "table": table_name,
                        "columns": [
                            {"name": col[0], "type": col[1], "nullable": col[2], "default": col[3]} for col in columns
                        ],
                    },
                }
            )
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})

    def get_table_sample(self, table_name: str, limit: int = 5) -> str:
        """Function to get sample records from a table
        :param table_name: Name of the table to sample
        :param limit: Number of records to return (max 100)
        :return: JSON string containing sample records
        """
        try:
            schema_query = "SELECT TABLE_SCHEMA FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = %s"
            results = self._execute_query(schema_query, (table_name,))
            if not results:
                return json.dumps({"status": "error", "message": f"Table {table_name} not found"})

            schema_name = results[0][0]
            sample_query = f"SELECT * FROM {schema_name}.{table_name} LIMIT {min(limit, 100)}"
            rows = self._execute_query(sample_query)

            headers = [desc[0] for desc in self.cursor.description]
            return json.dumps({"status": "success", "samples": [dict(zip(headers, row)) for row in rows]}, default=str)
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})

    def get_table_stats(self, table_name: str) -> str:
        """Function to get basic statistics about a table
        :param table_name: Name of the table to analyze
        :return: JSON string containing table statistics
        """
        try:
            schema_query = "SELECT TABLE_SCHEMA FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = %s"
            results = self._execute_query(schema_query, (table_name,))
            if not results:
                return json.dumps({"status": "error", "message": f"Table {table_name} not found"})

            schema_name = results[0][0]
            stats_query = f"""
                SELECT 
                    COUNT(*) as row_count,
                    COUNT(DISTINCT __time) as unique_times,
                    MIN(__time) as earliest_time,
                    MAX(__time) as latest_time
                FROM {schema_name}.{table_name}
            """
            stats = self._execute_query(stats_query)

            return json.dumps(
                {
                    "status": "success",
                    "stats": {
                        "schema": schema_name,
                        "table": table_name,
                        "row_count": stats[0][0],
                        "unique_timestamps": stats[0][1],
                        "time_range": {"earliest": str(stats[0][2]), "latest": str(stats[0][3])},
                    },
                }
            )
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})

    def run_query(self, query: str) -> str:
        """Function to run a custom SQL query
        :param query: SQL query to execute
        :return: JSON string containing query results
        """
        try:
            if "limit" not in query.lower():
                query = f"{query} LIMIT 100"

            rows = self._execute_query(query)
            if not rows:
                return json.dumps({"status": "success", "message": "Query returned no results"})

            headers = [desc[0] for desc in self.cursor.description]
            return json.dumps({"status": "success", "results": [dict(zip(headers, row)) for row in rows]}, default=str)
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e), "query": query})