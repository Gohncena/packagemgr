#!/usr/bin/env python3
"""
PyPkg TUI - A package manager with an aptitude-like text interface
"""

import curses
import sys
from pathlib import Path
from typing import List, Optional, Dict, Tuple
from dataclasses import dataclass
from enum import Enum

# Import the package manager core (assumes the previous code is in a module)
# For standalone use, you'd include the classes from the previous artifact
import json
import tarfile
import requests
import shutil


@dataclass
class Package:
    """Represents a package with metadata"""
    name: str
    version: str
    description: str
    dependencies: List[str]
    files: List[str]
    install_path: str = "/opt/pypkg"
    
    def to_dict(self):
        return {
            'name': self.name,
            'version': self.version,
            'description': self.description,
            'dependencies': self.dependencies,
            'files': self.files,
            'install_path': self.install_path
        }
    
    @staticmethod
    def from_dict(data: dict):
        return Package(**data)


class PackageDatabase:
    """Manages installed package tracking"""
    
    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = str(Path.home() / ".pypkg" / "installed.json")
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.packages: Dict[str, Package] = {}
        self.load()
    
    def load(self):
        if self.db_path.exists():
            with open(self.db_path, 'r') as f:
                data = json.load(f)
                self.packages = {
                    name: Package.from_dict(pkg_data)
                    for name, pkg_data in data.items()
                }
    
    def save(self):
        with open(self.db_path, 'w') as f:
            data = {name: pkg.to_dict() for name, pkg in self.packages.items()}
            json.dump(data, f, indent=2)
    
    def add(self, package: Package):
        self.packages[package.name] = package
        self.save()
    
    def remove(self, package_name: str):
        if package_name in self.packages:
            del self.packages[package_name]
            self.save()
    
    def get(self, package_name: str) -> Optional[Package]:
        return self.packages.get(package_name)
    
    def is_installed(self, package_name: str) -> bool:
        return package_name in self.packages
    
    def list_all(self) -> List[Package]:
        return list(self.packages.values())


class Repository:
    """Manages package repository"""
    
    def __init__(self, repo_url: str = "https://raw.githubusercontent.com/Gohncena/packagemgr/main/packages"):
        self.repo_url = repo_url.rstrip('/')
        self.cache_dir = Path.home() / ".pypkg" / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.package_cache = {}
    
    def fetch_package_list(self) -> Dict[str, Package]:
        if self.package_cache:
            return self.package_cache
        
        try:
            index_url = f"{self.repo_url}/index.json"
            response = requests.get(index_url, timeout=10)
            if response.status_code == 200:
                index = response.json()
                for pkg_info in index:
                    pkg = Package(
                        name=pkg_info['name'],
                        version=pkg_info['version'],
                        description=pkg_info.get('description', ''),
                        dependencies=pkg_info.get('dependencies', []),
                        files=[]
                    )
                    self.package_cache[pkg.name] = pkg
                return self.package_cache
        except:
            pass
        
        self.package_cache = {
            "sl": Package(
                name="sl",
                version="5.0.2",
                description="Steam Locomotive - displays a steam locomotive",
                dependencies=[],
                files=[]
            )
        }
        return self.package_cache
    
    def get_package_info(self, package_name: str, version: str = None) -> Optional[Package]:
        if package_name in self.package_cache:
            cached = self.package_cache[package_name]
            if version is None or cached.version == version:
                return cached
        
        if version:
            requirements_url = f"{self.repo_url}/{package_name}/{version}/requirements.txt"
        else:
            return None
        
        try:
            response = requests.get(requirements_url, timeout=10)
            if response.status_code == 200:
                dependencies = [line.strip() for line in response.text.strip().split('\n') if line.strip()]
                pkg = Package(
                    name=package_name,
                    version=version,
                    description=f"Package {package_name}",
                    dependencies=dependencies,
                    files=[]
                )
                self.package_cache[package_name] = pkg
                return pkg
        except:
            pass
        
        return None


class PackageAction(Enum):
    NONE = 0
    INSTALL = 1
    REMOVE = 2
    PURGE = 3


@dataclass
class PackageListItem:
    """Represents a package in the TUI list"""
    package: Package
    installed: bool
    action: PackageAction = PackageAction.NONE
    
    def get_status_char(self) -> str:
        if self.action == PackageAction.INSTALL:
            return 'i'
        elif self.action == PackageAction.REMOVE:
            return 'd'
        elif self.action == PackageAction.PURGE:
            return 'p'
        elif self.installed:
            return 'i'
        else:
            return ' '
    
    def get_display_line(self, width: int) -> str:
        status = self.get_status_char()
        name_width = 20
        version_width = 12
        
        name = self.package.name[:name_width].ljust(name_width)
        version = self.package.version[:version_width].ljust(version_width)
        desc_width = width - name_width - version_width - 6
        desc = self.package.description[:desc_width] if desc_width > 0 else ""
        
        return f"{status} {name} {version} {desc}"


class PackageManagerTUI:
    """TUI for package management"""
    
    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.db = PackageDatabase()
        self.repo = Repository()
        self.packages: List[PackageListItem] = []
        self.current_index = 0
        self.scroll_offset = 0
        self.search_query = ""
        self.status_message = "Welcome to PyPkg TUI"
        self.show_help = False
        
        # Initialize colors
        curses.start_color()
        curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_WHITE)  # Selected
        curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)  # Installed
        curses.init_pair(3, curses.COLOR_YELLOW, curses.COLOR_BLACK) # To install
        curses.init_pair(4, curses.COLOR_RED, curses.COLOR_BLACK)    # To remove
        curses.init_pair(5, curses.COLOR_CYAN, curses.COLOR_BLACK)   # Headers
        
        self.load_packages()
    
    def load_packages(self):
        """Load available and installed packages"""
        self.status_message = "Loading packages..."
        self.stdscr.refresh()
        
        available = self.repo.fetch_package_list()
        
        self.packages = []
        for name, pkg in available.items():
            installed = self.db.is_installed(name)
            self.packages.append(PackageListItem(pkg, installed))
        
        self.packages.sort(key=lambda x: x.package.name)
        self.status_message = f"Loaded {len(self.packages)} packages"
    
    def draw_header(self):
        """Draw the header bar"""
        height, width = self.stdscr.getmaxyx()
        header = " PyPkg - Package Manager ".center(width)
        self.stdscr.attron(curses.color_pair(5) | curses.A_BOLD)
        self.stdscr.addstr(0, 0, header[:width-1])
        self.stdscr.attroff(curses.color_pair(5) | curses.A_BOLD)
    
    def draw_package_list(self):
        """Draw the package list"""
        height, width = self.stdscr.getmaxyx()
        list_height = height - 5  # Leave room for header, status, footer
        
        # Draw column headers
        self.stdscr.attron(curses.color_pair(5))
        self.stdscr.addstr(1, 0, "  Name                 Version      Description".ljust(width-1))
        self.stdscr.attroff(curses.color_pair(5))
        
        # Draw packages
        for i in range(list_height):
            list_index = i + self.scroll_offset
            if list_index >= len(self.packages):
                break
            
            item = self.packages[list_index]
            y = i + 2
            
            # Determine color
            color = curses.color_pair(0)
            if item.action == PackageAction.INSTALL:
                color = curses.color_pair(3)
            elif item.action in (PackageAction.REMOVE, PackageAction.PURGE):
                color = curses.color_pair(4)
            elif item.installed:
                color = curses.color_pair(2)
            
            # Highlight selected item
            if list_index == self.current_index:
                color |= curses.color_pair(1) | curses.A_BOLD
            
            line = item.get_display_line(width)
            self.stdscr.attron(color)
            try:
                self.stdscr.addstr(y, 0, line[:width-1].ljust(width-1))
            except:
                pass
            self.stdscr.attroff(color)
    
    def draw_package_details(self):
        """Draw details of selected package"""
        height, width = self.stdscr.getmaxyx()
        details_y = height - 3
        
        if 0 <= self.current_index < len(self.packages):
            item = self.packages[self.current_index]
            pkg = item.package
            
            status = "Installed" if item.installed else "Not installed"
            if item.action == PackageAction.INSTALL:
                status = "Will be installed"
            elif item.action == PackageAction.REMOVE:
                status = "Will be removed"
            
            details = f"{pkg.name} {pkg.version} - {status}"
            if pkg.dependencies:
                details += f" | Deps: {', '.join(pkg.dependencies)}"
            
            self.stdscr.addstr(details_y, 0, details[:width-1].ljust(width-1))
    
    def draw_status_bar(self):
        """Draw the status bar"""
        height, width = self.stdscr.getmaxyx()
        status_y = height - 2
        
        self.stdscr.attron(curses.color_pair(5))
        self.stdscr.addstr(status_y, 0, self.status_message[:width-1].ljust(width-1))
        self.stdscr.attroff(curses.color_pair(5))
    
    def draw_footer(self):
        """Draw the footer with key bindings"""
        height, width = self.stdscr.getmaxyx()
        footer_y = height - 1
        
        footer = "q:Quit  +:Install  -:Remove  g:Go/Apply  u:Update  /:Search  ?:Help"
        self.stdscr.attron(curses.A_BOLD)
        self.stdscr.addstr(footer_y, 0, footer[:width-1].ljust(width-1))
        self.stdscr.attroff(curses.A_BOLD)
    
    def draw_help(self):
        """Draw help screen"""
        height, width = self.stdscr.getmaxyx()
        
        help_text = [
            "PyPkg TUI - Help",
            "",
            "Navigation:",
            "  ↑/k     - Move up",
            "  ↓/j     - Move down",
            "  PgUp    - Page up",
            "  PgDn    - Page down",
            "  Home    - Go to first package",
            "  End     - Go to last package",
            "",
            "Actions:",
            "  +       - Mark package for installation",
            "  -       - Mark package for removal",
            "  g       - Apply pending changes (Go)",
            "  u       - Update package list",
            "  /       - Search packages",
            "",
            "Other:",
            "  ?       - Show this help",
            "  q       - Quit",
            "",
            "Package Status:",
            "  i       - Installed",
            "  (space) - Not installed",
            "",
            "Press any key to close help..."
        ]
        
        start_y = max(0, (height - len(help_text)) // 2)
        start_x = max(0, (width - 60) // 2)
        
        # Draw box
        for i, line in enumerate(help_text):
            y = start_y + i
            if 0 <= y < height:
                self.stdscr.addstr(y, start_x, line[:60].ljust(60))
    
    def draw(self):
        """Draw the entire interface"""
        self.stdscr.clear()
        
        if self.show_help:
            self.draw_help()
        else:
            self.draw_header()
            self.draw_package_list()
            self.draw_package_details()
            self.draw_status_bar()
            self.draw_footer()
        
        self.stdscr.refresh()
    
    def handle_input(self, key):
        """Handle keyboard input"""
        height, width = self.stdscr.getmaxyx()
        list_height = height - 5
        
        if self.show_help:
            self.show_help = False
            return True
        
        if key == ord('q'):
            return False
        
        elif key == ord('?'):
            self.show_help = True
        
        elif key == curses.KEY_UP or key == ord('k'):
            if self.current_index > 0:
                self.current_index -= 1
                if self.current_index < self.scroll_offset:
                    self.scroll_offset = self.current_index
        
        elif key == curses.KEY_DOWN or key == ord('j'):
            if self.current_index < len(self.packages) - 1:
                self.current_index += 1
                if self.current_index >= self.scroll_offset + list_height:
                    self.scroll_offset = self.current_index - list_height + 1
        
        elif key == curses.KEY_PPAGE:  # Page Up
            self.current_index = max(0, self.current_index - list_height)
            self.scroll_offset = max(0, self.scroll_offset - list_height)
        
        elif key == curses.KEY_NPAGE:  # Page Down
            self.current_index = min(len(self.packages) - 1, 
                                    self.current_index + list_height)
            self.scroll_offset = min(len(self.packages) - list_height,
                                    self.scroll_offset + list_height)
        
        elif key == curses.KEY_HOME:
            self.current_index = 0
            self.scroll_offset = 0
        
        elif key == curses.KEY_END:
            self.current_index = len(self.packages) - 1
            self.scroll_offset = max(0, len(self.packages) - list_height)
        
        elif key == ord('+'):
            self.mark_for_install()
        
        elif key == ord('-'):
            self.mark_for_remove()
        
        elif key == ord('g'):
            self.apply_changes()
        
        elif key == ord('u'):
            self.load_packages()
        
        elif key == ord('/'):
            self.search_packages()
        
        return True
    
    def mark_for_install(self):
        """Mark current package for installation"""
        if 0 <= self.current_index < len(self.packages):
            item = self.packages[self.current_index]
            if not item.installed:
                item.action = PackageAction.INSTALL
                self.status_message = f"Marked {item.package.name} for installation"
            else:
                self.status_message = f"{item.package.name} is already installed"
    
    def mark_for_remove(self):
        """Mark current package for removal"""
        if 0 <= self.current_index < len(self.packages):
            item = self.packages[self.current_index]
            if item.installed:
                item.action = PackageAction.REMOVE
                self.status_message = f"Marked {item.package.name} for removal"
            else:
                self.status_message = f"{item.package.name} is not installed"
    
    def apply_changes(self):
        """Apply pending package changes"""
        to_install = [item for item in self.packages if item.action == PackageAction.INSTALL]
        to_remove = [item for item in self.packages if item.action == PackageAction.REMOVE]
        
        if not to_install and not to_remove:
            self.status_message = "No changes to apply"
            return
        
        # Show confirmation
        self.status_message = f"Apply {len(to_install)} installs, {len(to_remove)} removals? (y/n)"
        self.draw()
        
        key = self.stdscr.getch()
        if key != ord('y') and key != ord('Y'):
            self.status_message = "Changes cancelled"
            return
        
        # Apply changes
        for item in to_install:
            self.install_package(item)
        
        for item in to_remove:
            self.remove_package(item)
        
        self.load_packages()
    
    def install_package(self, item: PackageListItem):
        """Install a package"""
        self.status_message = f"Installing {item.package.name}..."
        self.draw()
        
        try:
            pkg_path = self.repo.download_package(item.package)
            install_root = Path.home() / ".pypkg" / "installed"
            extract_dir = install_root / item.package.name / item.package.version
            extract_dir.mkdir(parents=True, exist_ok=True)
            
            with tarfile.open(pkg_path, "r:*") as tar:
                tar.extractall(extract_dir)
            
            self.db.add(item.package)
            item.installed = True
            item.action = PackageAction.NONE
            self.status_message = f"Successfully installed {item.package.name}"
        except Exception as e:
            self.status_message = f"Error installing {item.package.name}: {str(e)}"
    
    def remove_package(self, item: PackageListItem):
        """Remove a package"""
        self.status_message = f"Removing {item.package.name}..."
        self.draw()
        
        try:
            install_root = Path.home() / ".pypkg" / "installed"
            pkg_dir = install_root / item.package.name / item.package.version
            if pkg_dir.exists():
                shutil.rmtree(pkg_dir)
            
            self.db.remove(item.package.name)
            item.installed = False
            item.action = PackageAction.NONE
            self.status_message = f"Successfully removed {item.package.name}"
        except Exception as e:
            self.status_message = f"Error removing {item.package.name}: {str(e)}"
    
    def search_packages(self):
        """Search for packages"""
        height, width = self.stdscr.getmaxyx()
        search_y = height - 2
        
        curses.echo()
        self.stdscr.addstr(search_y, 0, "Search: ".ljust(width-1))
        query = self.stdscr.getstr(search_y, 8, 50).decode('utf-8')
        curses.noecho()
        
        if query:
            for i, item in enumerate(self.packages):
                if query.lower() in item.package.name.lower() or \
                   query.lower() in item.package.description.lower():
                    self.current_index = i
                    self.scroll_offset = max(0, i - 5)
                    self.status_message = f"Found: {item.package.name}"
                    return
            
            self.status_message = f"No packages matching '{query}'"
        else:
            self.status_message = "Search cancelled"
    
    def run(self):
        """Main TUI loop"""
        curses.curs_set(0)  # Hide cursor
        self.stdscr.keypad(True)
        
        while True:
            self.draw()
            key = self.stdscr.getch()
            if not self.handle_input(key):
                break


def main():
    """Main entry point"""
    try:
        curses.wrapper(lambda stdscr: PackageManagerTUI(stdscr).run())
    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(0)


if __name__ == "__main__":
    main()
