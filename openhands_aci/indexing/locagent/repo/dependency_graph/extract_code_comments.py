import libcst as cst
from typing import Dict, List, Optional, Union
import re
from .build_graph import (
    NODE_TYPE_CLASS, NODE_TYPE_FUNCTION
)

try:
    from libcst._exceptions import ParserError
except ImportError:
    # Fallback for different libcst versions
    ParserError = Exception

class CommentExtractor(cst.CSTVisitor):
    """Extracts comments from classes and functions using CST parsing."""

    def __init__(self):
        self.comments = {}
        self.current_module = None
        self.source_lines = []
        self.class_stack = []  # Track nested classes

    def extract_comments(self, source_code: str) -> Dict[str, Dict[str, Union[str, List[str]]]]:
        """
        Extract comments from Python source code.

        Args:
            source_code: Python source code as string

        Returns:
            Dictionary with structure:
            {
                'class': {
                    'ClassName': {
                        'docstring': 'class docstring',
                        'post_def_comments': ""
                    },
                    'OuterClass.InnerClass': {
                        'docstring': 'nested class docstring',
                        'post_def_comments': ""
                    }
                },
                'function': {
                    'function_name': {
                        'docstring': 'function docstring',
                        'post_def_comments': ['# comment after def']
                    },
                    'ClassName.method_name': {
                        'docstring': 'method docstring',
                        'post_def_comments': ['# comment after def']
                    }
                }
            }
        """
        self.source_lines = source_code.split('\n')
        self.comments = {NODE_TYPE_CLASS: {}, NODE_TYPE_FUNCTION: {}}
        self.class_stack = []

        try:
            tree = cst.parse_expression(source_code) if source_code.strip().startswith('(') else cst.parse_module(source_code)
            tree.visit(self)
        except Exception as e:
            print(f"Parser error: {e}")
            return self.comments

        return self.comments

    def visit_ClassDef(self, node: cst.ClassDef) -> Optional[bool]:
        """Visit class definitions and extract comments."""
        class_name = node.name.value

        # Build qualified name for nested classes
        if self.class_stack:
            qualified_name = '.'.join(self.class_stack) + '.' + class_name
        else:
            qualified_name = class_name

        # Extract docstring
        docstring = self._extract_docstring(node.body)

        self.comments[NODE_TYPE_CLASS][qualified_name] = {
            'docstring': docstring,
            'post_def_comments': ""
        }

        # Push current class onto stack for nested classes
        self.class_stack.append(class_name)

    def leave_ClassDef(self, original_node: cst.ClassDef) -> None:
        """Pop class from stack when leaving class definition."""
        if self.class_stack:
            self.class_stack.pop()

    def visit_FunctionDef(self, node: cst.FunctionDef) -> Optional[bool]:
        """Visit function definitions and extract comments."""
        func_name = node.name.value

        # Build qualified name for methods (include class name if inside a class)
        if self.class_stack:
            qualified_name = '.'.join(self.class_stack) + '.' + func_name
        else:
            qualified_name = func_name

        # Extract docstring
        docstring = self._extract_docstring(node.body)

        # Extract comments immediately following function definition
        post_def_comments = self._extract_post_def_comments(node)

        self.comments[NODE_TYPE_FUNCTION][qualified_name] = {
            'docstring': docstring,
            'post_def_comments': post_def_comments
        }

        # Continue visiting child nodes (for nested functions)

    def _extract_docstring(self, body: cst.BaseSuite) -> Optional[str]:
        """Extract docstring from function or class body."""
        if isinstance(body, cst.SimpleStatementSuite):
            return None

        if isinstance(body, cst.IndentedBlock):
            statements = body.body
            if statements and isinstance(statements[0], cst.SimpleStatementLine):
                first_stmt = statements[0].body[0]
                if isinstance(first_stmt, cst.Expr) and isinstance(first_stmt.value, cst.SimpleString):
                    # Remove quotes and process escape sequences
                    docstring = first_stmt.value.value
                    # Remove triple quotes
                    for quote in ['"""', "'''"]:
                        if docstring.startswith(quote) and docstring.endswith(quote):
                            docstring = docstring[3:-3]
                            break
                    # Remove single quotes
                    for quote in ['"', "'"]:
                        if docstring.startswith(quote) and docstring.endswith(quote):
                            docstring = docstring[1:-1]
                            break
                    return docstring.strip()
        return None


    def _extract_post_def_comments(self, node: cst.FunctionDef) -> List[str]:
        """Extract # comments that immediately follow function definition until first code line."""
        comments = []

        # Get the line number where function definition ends
        start_line = self._get_line_number(node)
        if start_line is None:
            return comments

        # Find the end of the function signature (including parameters and return annotation)
        func_end_line = start_line
        paren_count = 0
        in_params = False

        for i, line in enumerate(self.source_lines[start_line - 1:], start_line):
            if '(' in line:
                paren_count += line.count('(')
                in_params = True
            if ')' in line:
                paren_count -= line.count(')')
                if in_params and paren_count == 0:
                    func_end_line = i
                    break
            if ':' in line and paren_count == 0:
                func_end_line = i
                break

        # Look for comments immediately after function definition
        current_line = func_end_line
        found_docstring = False

        while current_line < len(self.source_lines):
            line = self.source_lines[current_line].strip()

            # Skip empty lines
            if not line:
                current_line += 1
                continue

            # If we haven't found a docstring yet and this is a comment, add it
            if not found_docstring and line.startswith('#'):
                comments.append(line[1:].strip())
                current_line += 1
                continue

            # If it's the start of a docstring, mark that we found it and skip
            if line.startswith(('"""', "'''")):
                found_docstring = True
                # Skip multi-line docstring
                quote = line[:3]
                if line.count(quote) >= 2:
                    # Single line docstring
                    current_line += 1
                else:
                    # Multi-line docstring - find the end
                    current_line += 1
                    while current_line < len(self.source_lines):
                        if quote in self.source_lines[current_line]:
                            current_line += 1
                            break
                        current_line += 1
                # After docstring, stop looking for comments
                break

            # If we found a docstring and this is a comment, don't add it
            if found_docstring and line.startswith('#'):
                break

            # If it's any other code (not comment, not docstring), stop looking
            if line and not line.startswith('#'):
                break

            current_line += 1

        return comments

    def _get_line_number(self, node: cst.CSTNode) -> Optional[int]:
        """Get approximate line number for a node."""
        # Fallback: try to find the node in source by matching text
        if hasattr(node, 'name'):
            node_name = node.name.value
            for i, line in enumerate(self.source_lines, 1):
                if node_name in line and ('def ' in line or 'class ' in line):
                    return i
        return None


def extract_code_comments(source_code: str) -> Dict[str, Dict[str, Union[str, List[str]]]]:
    """
    Main function to extract comments from Python source code.

    Args:
        source_code: Python source code as string

    Returns:
        Dictionary containing extracted comments for classes and functions
    """
    extractor = CommentExtractor()
    return extractor.extract_comments(source_code)


# Example usage and test
if __name__ == "__main__":
    sample_code = '''
class MyClass:  # This is a class comment
    """This is a class docstring."""

    def __init__(self):
        pass

    def my_method(self, param1: int, param2: str) -> None:  # Method comment
        """This is a method docstring."""
        # This comment should be extracted
        # This is another post-def comment

        print("Hello World")  # This should not be extracted

class MyClass2:  # This is a class comment
    """This is a class docstring."""

    class ObjectReferencePart:
        """Details about a table alias."""
        part: str  # Name of the part
        segment: str  # Segment containing the part

    def my_method(self, param1: int, param2: str) -> None:  # Method comment
        """This is a method docstring."""
        # This comment should be extracted
        # This is another post-def comment

        print("Hello World")  # This should not be extracted

def standalone_function(x, y):
    # Comment right after function definition
    # Another comment before docstring
    """Function docstring here."""

    # This comment is after docstring, should not be extracted
    return x + y

def simple_function():  # Inline comment
    # Post-def comment 1
    # Post-def comment 2

    def test_inside():
        # this is a test inside function
        pass
    return "simple"
'''

    comments = extract_code_comments(sample_code)

    print("Extracted Comments:")
    print("=" * 50)

    for class_name, class_info in comments[NODE_TYPE_CLASS].items():
        print(f"\nClass: {class_name}")
        print(f"  Docstring: {class_info['docstring']}")

    for func_name, func_info in comments[NODE_TYPE_FUNCTION].items():
        print(f"\nFunction: {func_name}")
        print(f"  Docstring: {func_info['docstring']}")
        print(f"  Post-def comments: {func_info['post_def_comments']}")
