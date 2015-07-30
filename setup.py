from setuptools import setup, find_packages
setup(
    name = "cadvisor-metrics",
    version = "0.1.3",
    packages = find_packages(),

    install_requires=[
        "redis",
        "falcon",
        "gunicorn",
        "py-dateutil",
        "requests",
    ],

    author = "Ben Uphoff",
    author_email = "ben@catalyze.io",
    description = "cAdvisor metrics collection from Docker containers running on multiple hosts.",
    license = "Apache 2.0",
    keywords = "docker cadvisor metrics container",
    url = "https://github.com/catalyzeio/cadvisor-metrics",

)
