import os
from git.repo import Repo
from git.repo.fun import is_git_dir


class GitRepository(object):
    """
    Git repository management
    """

    def __init__(self, local_path, repo_url, branch='master'):
        self.local_path = local_path
        self.repo_url = repo_url
        self.repo = None
        self.initial(self.repo_url, branch)

    def initial(self, repo_url, branch):
        """
        Initialize git repository
        :param repo_url:
        :param branch:
        :return:
        """
        if not os.path.exists(self.local_path):
            os.makedirs(self.local_path)
        git_local_path = os.path.join(self.local_path, '.git')
        if not is_git_dir(git_local_path):
            self.repo = Repo.clone_from(repo_url, to_path=self.local_path, branch=branch)
        else:
            self.repo = Repo(self.local_path)

    def pull(self):
        """
        Pull latest code from remote
        :return:
        """
        self.repo.git.pull()

    def branches(self):
        """
        Get all branches
        :return:
        """
        branches = self.repo.remote().refs
        return [item.remote_head for item in branches if item.remote_head not in ['HEAD', ]]

    def commits(self):
        """
        Get all commit records
        :return:
        """
        commit_log = self.repo.git.log('--pretty={"commit":"%h","author":"%an","summary":"%s","date":"%cd"}',
                                       max_count=50,
                                       date='format:%Y-%m-%d %H:%M')
        log_list = commit_log.split("\n")
        return [eval(item) for item in log_list]

    def tags(self):
        """
        Get all tags
        :return:
        """
        return [tag.name for tag in self.repo.tags]

    def tags_exists(self, tag):
        """
        Check if tag exists
        :return:
        """
        return tag in self.tags()

    def change_to_branch(self, branch):
        """
        Switch branch
        :param branch:
        :return:
        """
        self.repo.git.checkout(branch)

    def change_to_commit(self, branch, commit):
        """
        Switch commit
        :param branch:
        :param commit:
        :return:
        """
        self.change_to_branch(branch=branch)
        self.repo.git.reset('--hard', commit)

    def change_to_tag(self, tag):
        """
        Switch tag
        :param tag:
        :return:
        """
        self.repo.git.checkout(tag)

# if __name__ == '__main__':
# local_path = os.path.join('codes', 't1')
# repo = GitRepository(local_path, remote_path)
# branch_list = repo.branches()
# print(branch_list)
# repo.change_to_branch('dev')
# repo.pull()