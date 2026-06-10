#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DNS to Hosts File Generator - Optimized Version
深度优化版：多线程、进度条、缓存、重试机制、详细日志
"""

import socket
import subprocess
import re
import sys
import platform
import time
import json
import argparse
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Set, Tuple, Dict
from dataclasses import dataclass, asdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
import threading

# ============================================================
# CONFIGURATION
# ============================================================

@dataclass
class Config:
    """Configuration for DNS resolver"""
    output_file: str = "hosts"
    domains_file: Optional[str] = None  # External domains file
    dns_timeout: float = 3.0
    ping_timeout: float = 5.0
    sleep_interval: float = 0.0  # No sleep needed with threading
    max_workers: int = 50  # Concurrent DNS queries
    retry_attempts: int = 2
    cache_file: str = "dns_cache.json"
    use_cache: bool = True
    skip_ping_fallback: bool = False
    log_dir: str = "logs"
    verbose: bool = False


# ============================================================
# STATISTICS
# ============================================================

@dataclass
class ResolveStats:
    """Statistics for DNS resolution"""
    total: int = 0
    resolved: int = 0
    failed: int = 0
    cached: int = 0
    dns_success: int = 0
    ping_success: int = 0

    def __str__(self) -> str:
        success_rate = (self.resolved / self.total * 100) if self.total > 0 else 0
        return (
            f"Total domains: {self.total}\n"
            f"Resolved: {self.resolved} ({success_rate:.1f}%)\n"
            f"  - From DNS: {self.dns_success}\n"
            f"  - From Ping: {self.ping_success}\n"
            f"  - From Cache: {self.cached}\n"
            f"Failed: {self.failed}"
        )


# ============================================================
# DNS CACHE
# ============================================================

class DNSCache:
    """Thread-safe DNS resolution cache"""

    def __init__(self, cache_file: str):
        self.cache_file = Path(cache_file)
        self.cache: Dict[str, str] = {}
        self.lock = threading.Lock()
        self.load()

    def load(self) -> None:
        """Load cache from file"""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    self.cache = json.load(f)
            except Exception:
                self.cache = {}

    def save(self) -> None:
        """Save cache to file"""
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, indent=2)
        except Exception as e:
            logging.error(f"Failed to save cache: {e}")

    def get(self, domain: str) -> Optional[str]:
        """Get cached IP for domain"""
        with self.lock:
            return self.cache.get(domain.lower())

    def set(self, domain: str, ip: str) -> None:
        """Cache domain -> IP mapping"""
        with self.lock:
            self.cache[domain.lower()] = ip


# ============================================================
# DOMAIN PARSER
# ============================================================

def parse_domains_from_text(raw: str) -> List[str]:
    """Parse domains from text content"""
    seen: Set[str] = set()
    result: List[str] = []

    for line in raw.splitlines():
        domain = line.strip()
        # Skip empty lines and comments
        if not domain or domain.startswith("#"):
            continue

        domain_lower = domain.lower()
        if domain_lower not in seen:
            seen.add(domain_lower)
            result.append(domain)

    return result


def parse_domains_from_file(file_path: str) -> List[str]:
    """Parse domains from external file"""
    with open(file_path, 'r', encoding='utf-8') as f:
        return parse_domains_from_text(f.read())


# ============================================================
# DNS RESOLVERS
# ============================================================

def resolve_dns(domain: str, timeout: float = 3.0) -> Optional[str]:
    """Resolve domain via DNS, prefer IPv4"""
    try:
        socket.setdefaulttimeout(timeout)
        infos = socket.getaddrinfo(domain, None)

        # Prefer IPv4
        for info in infos:
            if info[0] == socket.AF_INET:
                return info[4][0]

        # Fallback to first result
        if infos:
            return infos[0][4][0]
    except Exception:
        pass
    return None


def resolve_ping(domain: str, timeout: float = 5.0) -> Optional[str]:
    """Fallback: extract IP from ping output"""
    try:
        os_name = platform.system()
        if os_name == "Windows":
            cmd = ["ping", "-n", "1", "-w", "2000", domain]
        else:
            cmd = ["ping", "-c", "1", "-W", "2", domain]

        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            text=True
        )

        # Match IP patterns
        patterns = [
            r'\[(\d+\.\d+\.\d+\.\d+)\]',
            r'\((\d+\.\d+\.\d+\.\d+)\)',
            r'from (\d+\.\d+\.\d+\.\d+)',
            r'Reply from (\d+\.\d+\.\d+\.\d+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, proc.stdout)
            if match:
                return match.group(1)
    except Exception:
        pass
    return None


# ============================================================
# RESOLVER WITH RETRY
# ============================================================

def resolve_domain(
    domain: str,
    cache: DNSCache,
    config: Config,
    stats: ResolveStats
) -> Tuple[str, Optional[str]]:
    """
    Resolve single domain with cache, retry, and fallback

    Returns:
        (domain, ip_or_none)
    """
    # Check cache first
    if config.use_cache:
        cached_ip = cache.get(domain)
        if cached_ip:
            stats.cached += 1
            return (domain, cached_ip)

    ip = None

    # Try DNS with retries
    for attempt in range(config.retry_attempts):
        ip = resolve_dns(domain, config.dns_timeout)
        if ip:
            stats.dns_success += 1
            break
        time.sleep(0.1 * attempt)  # Exponential backoff

    # Fallback to ping
    if not ip and not config.skip_ping_fallback:
        ip = resolve_ping(domain, config.ping_timeout)
        if ip:
            stats.ping_success += 1

    # Update cache and stats
    if ip:
        if config.use_cache:
            cache.set(domain, ip)
        stats.resolved += 1
    else:
        stats.failed += 1

    return (domain, ip)


# ============================================================
# HOSTS FILE WRITER
# ============================================================

class HostsWriter:
    """Thread-safe hosts file writer"""

    def __init__(self, output_file: str, total_domains: int):
        self.output_file = Path(output_file)
        self.lock = threading.Lock()
        self.written_count = 0
        self._write_header(total_domains)

    def _write_header(self, total_domains: int) -> None:
        """Write hosts file header"""
        with open(self.output_file, 'w', encoding='utf-8') as f:
            f.write("# " + "="*58 + "\n")
            f.write("# Hosts file generated by dns-to-host-optimized.py\n")
            f.write(f"# Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"# Total domains: {total_domains}\n")
            f.write("# " + "="*58 + "\n\n")
            f.write("127.0.0.1           localhost\n")
            f.write("::1                 localhost\n\n")

    def write_entry(self, domain: str, ip: str) -> None:
        """Write single hosts entry (thread-safe)"""
        with self.lock:
            with open(self.output_file, 'a', encoding='utf-8') as f:
                line = f"{ip:<20} {domain}\n"
                f.write(line)
                f.flush()
            self.written_count += 1


# ============================================================
# MAIN RESOLVER
# ============================================================

class DNSResolver:
    """Main DNS resolver orchestrator"""

    def __init__(self, config: Config):
        self.config = config
        self.stats = ResolveStats()
        self.cache = DNSCache(config.cache_file) if config.use_cache else None
        self._setup_logging()

    def _setup_logging(self) -> None:
        """Setup logging system"""
        log_dir = Path(self.config.log_dir)
        log_dir.mkdir(exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = log_dir / f"dns_resolve_{timestamp}.log"

        level = logging.DEBUG if self.config.verbose else logging.INFO
        logging.basicConfig(
            level=level,
            format='%(asctime)s [%(levelname)s] %(message)s',
            handlers=[
                logging.FileHandler(log_file, encoding='utf-8'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)

        # Failed domains log
        self.failed_log = log_dir / f"failed_domains_{timestamp}.txt"

    def load_domains(self) -> List[str]:
        """Load domains from file or embedded list"""
        if self.config.domains_file:
            self.logger.info(f"Loading domains from: {self.config.domains_file}")
            return parse_domains_from_file(self.config.domains_file)
        else:
            # Try to load from external domains.txt if exists
            if Path("domains.txt").exists():
                self.logger.info("Loading domains from: domains.txt")
                return parse_domains_from_file("domains.txt")
            else:
                self.logger.error("No domains file found!")
                return []

    def resolve_all(self, domains: List[str]) -> None:
        """Resolve all domains with multithreading"""
        self.stats.total = len(domains)

        if self.stats.total == 0:
            self.logger.error("No domains to resolve!")
            return

        self.logger.info(f"Starting resolution of {self.stats.total} domains")
        self.logger.info(f"Max workers: {self.config.max_workers}")

        # Initialize hosts writer
        writer = HostsWriter(self.config.output_file, self.stats.total)

        # Thread pool execution
        with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
            # Submit all tasks
            futures = {
                executor.submit(
                    resolve_domain,
                    domain,
                    self.cache,
                    self.config,
                    self.stats
                ): domain
                for domain in domains
            }

            # Process results with progress bar
            with tqdm(total=self.stats.total, desc="Resolving", unit="domain") as pbar:
                for future in as_completed(futures):
                    domain, ip = future.result()

                    if ip:
                        writer.write_entry(domain, ip)
                        if self.config.verbose:
                            self.logger.debug(f"{domain} -> {ip}")
                    else:
                        # Log failed domain
                        with open(self.failed_log, 'a', encoding='utf-8') as f:
                            f.write(f"{domain}\n")

                    pbar.update(1)

        # Save cache
        if self.config.use_cache and self.cache:
            self.cache.save()

        # Verify written count
        if writer.written_count != self.stats.resolved:
            self.logger.warning(
                f"Write mismatch: {writer.written_count} written "
                f"vs {self.stats.resolved} resolved"
            )

    def print_summary(self) -> None:
        """Print resolution summary"""
        print("\n" + "="*60)
        print("Resolution Summary")
        print("="*60)
        print(self.stats)
        print("="*60)
        print(f"\nOutput file: {self.config.output_file}")
        print(f"Failed domains log: {self.failed_log}")
        if self.config.use_cache:
            print(f"Cache file: {self.config.cache_file}")
        print()


# ============================================================
# CLI
# ============================================================

def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='DNS to Hosts File Generator - Optimized Version',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                              # Use domains.txt
  %(prog)s -f mydomains.txt             # Custom domains file
  %(prog)s -j 100 --no-cache            # 100 threads, no cache
  %(prog)s --skip-ping -v               # Skip ping fallback, verbose
  %(prog)s -o custom_hosts              # Custom output file
        """
    )

    parser.add_argument(
        '-f', '--file',
        dest='domains_file',
        help='Domains file path (default: domains.txt)'
    )
    parser.add_argument(
        '-o', '--output',
        default='hosts',
        help='Output hosts file (default: hosts)'
    )
    parser.add_argument(
        '-j', '--jobs',
        type=int,
        default=50,
        help='Max concurrent workers (default: 50)'
    )
    parser.add_argument(
        '--dns-timeout',
        type=float,
        default=3.0,
        help='DNS timeout in seconds (default: 3.0)'
    )
    parser.add_argument(
        '--ping-timeout',
        type=float,
        default=5.0,
        help='Ping timeout in seconds (default: 5.0)'
    )
    parser.add_argument(
        '--retry',
        type=int,
        default=2,
        help='Retry attempts (default: 2)'
    )
    parser.add_argument(
        '--no-cache',
        action='store_true',
        help='Disable DNS cache'
    )
    parser.add_argument(
        '--skip-ping',
        action='store_true',
        help='Skip ping fallback'
    )
    parser.add_argument(
        '--cache-file',
        default='dns_cache.json',
        help='Cache file path (default: dns_cache.json)'
    )
    parser.add_argument(
        '--log-dir',
        default='logs',
        help='Log directory (default: logs)'
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Verbose output'
    )

    args = parser.parse_args()

    # Build config
    config = Config(
        output_file=args.output,
        domains_file=args.domains_file,
        dns_timeout=args.dns_timeout,
        ping_timeout=args.ping_timeout,
        max_workers=args.jobs,
        retry_attempts=args.retry,
        cache_file=args.cache_file,
        use_cache=not args.no_cache,
        skip_ping_fallback=args.skip_ping,
        log_dir=args.log_dir,
        verbose=args.verbose
    )

    try:
        # Create resolver
        resolver = DNSResolver(config)

        # Load domains
        domains = resolver.load_domains()

        if not domains:
            print("[ERROR] No domains found!")
            print("Please provide a domains file with -f or create domains.txt")
            return 1

        # Resolve all domains
        start_time = time.time()
        resolver.resolve_all(domains)
        elapsed = time.time() - start_time

        # Print summary
        resolver.print_summary()
        print(f"Time elapsed: {elapsed:.2f} seconds")
        print(f"Speed: {len(domains)/elapsed:.1f} domains/sec")

        return 0

    except KeyboardInterrupt:
        print("\n\n[Interrupted] Operation cancelled by user")
        return 130
    except Exception as e:
        print(f"[ERROR] {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())

